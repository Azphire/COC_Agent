"""Normal API import/binding checks, and read-only snapshots of historical records."""

import json
import sqlite3
from pathlib import Path

from scripts.prepare_batch41 import QUEUE, ROOT, digest, isolate, read, relative, write


def ok(response):
    if not response.is_success:
        raise ValueError(
            f"{response.request.method} {response.request.url.path}: "
            f"{response.status_code} {response.text[:1600]}"
        )
    if "ndjson" in response.headers.get("content-type", ""):
        return [json.loads(line) for line in response.text.splitlines() if line.strip()]
    return response.json()


def client_for(directory):
    from fastapi.testclient import TestClient

    from scripts.module_package import settings_for

    isolate(directory)
    from app.main import create_app

    return TestClient(
        create_app(settings_for(directory)),
        headers={"Authorization": "Bearer local-package-review"},
    )


def validate(entry):
    out = ROOT / entry["output_directory"]
    package_path = out / "package-reviewed.json"
    package = read(package_path)
    target = out / "validation-final"
    target.mkdir(exist_ok=True)
    receipt = target / "validation.json"
    prior = read(receipt) if receipt.exists() else {}
    with client_for(target) as client:
        imported = ok(client.post("/api/module-preparations/import", json=package))
        repeated = ok(client.post("/api/module-preparations/import", json=package))
        assert repeated["reused"] and repeated["preparation_id"] == imported["preparation_id"]
        assert imported["preparation"]["status"] == "approved"
        pid = imported["preparation_id"]
        prep = ok(client.get(f"/api/module-preparations/{pid}"))
        structure = ok(client.get(f"/api/module-preparations/{pid}/structure"))
        assert prep["package_bindable"] and structure["approved"]
        if prior.get("preparation_id") == pid:
            room_id = prior["room_id"]
        else:
            created = ok(
                client.post("/api/rooms", json={"name": f"第41批绑定验证 {entry['title']}"})
            )
            room_id = created["room"]["id"]
            write(receipt, {"status": "binding_pending", "preparation_id": pid, "room_id": room_id})
        bound = ok(
            client.patch(f"/api/rooms/{room_id}/module-preparation", json={"preparation_id": pid})
        )
        logs = ok(client.get(f"/api/rooms/{room_id}/logs"))
        events = logs.get("events", logs.get("logs", [])) if isinstance(logs, dict) else logs
        binding = next(e["payload"] for e in reversed(events) if e["type"] == "preparation.bound")
        assert binding["preparation_id"] == pid and binding["source_hash"] == entry["source_hash"]
        assert binding["title"] == package["title"]
        result = {
            "status": "passed",
            "package_sha256": imported["package_sha256"],
            "package_file_sha256": digest(package_path),
            "preparation_id": pid,
            "version": prep["version"],
            "snapshot_id": imported["snapshot_id"],
            "room_id": room_id,
            "binding": binding,
            "repeat_import_reused": True,
            "source_hash": entry["source_hash"],
            "gameplay_test": "not_run_here",
        }
        write(target / "bound-room.json", bound)
        write(target / "preparation.json", prep)
        write(target / "structure.json", structure)
        write(target / "load-info.json", imported)
        write(receipt, result)
    return result


def backup_database(source, destination):
    """SQLite's read-only backup reads a consistent image; no user SQL state is written."""
    destination = Path(destination)
    if destination.exists():
        return
    with sqlite3.connect(Path(source).as_uri() + "?mode=ro", uri=True) as src:
        with sqlite3.connect(destination) as dst:
            src.backup(dst)


def verify_existing(entry):
    candidates = [p for p in entry["existing_products"] if p["valid"]]
    if not candidates:
        raise ValueError("No matching audited historical package")
    records = read(QUEUE / "existing-records.json")
    record = next(r for r in records if f"/{entry['module_id']}/" in r["path"])
    source = (ROOT / record["path"]).parent
    target = ROOT / entry["output_directory"] / "existing-record-snapshot"
    target.mkdir(parents=True, exist_ok=True)
    for name in ("game.db", "knowledge.db"):
        backup_database(source / name, target / name)
    pids = [p["id"] for p in record["preparations"]]
    views = []
    with client_for(target) as client:
        for pid in pids:
            prep = ok(client.get(f"/api/module-preparations/{pid}"))
            structure = ok(client.get(f"/api/module-preparations/{pid}/structure"))
            assert prep["status"] == "approved" and prep["package_bindable"]
            assert prep["source_hash"] == entry["source_hash"]
            assert structure["approved"] and not structure["stale"]
            views.append({"preparation": prep, "structure": structure})
    result = {
        "status": "valid_existing",
        "original_database": relative(source / "game.db"),
        "checked_via": "normal preparation/structure API on a read-only SQLite backup",
        "original_unchanged": True,
        "records": views,
    }
    write(target.parent / "existing-verification.json", result)
    return result


def publish(entries):
    """Publish approved files through the official importer into configured application DB."""
    from fastapi.testclient import TestClient

    from app.config import Settings

    settings = Settings()
    if not settings.host_admin_token.get_secret_value():
        raise ValueError("Configured normal application has no host token")
    # Import-time default application is isolated; this explicit app uses normal settings.
    isolate(QUEUE / "publish-bootstrap")
    from app.main import create_app

    results = {}
    with TestClient(
        create_app(settings),
        headers={
            "Authorization": "Bearer " + settings.host_admin_token.get_secret_value(),
        },
    ) as client:
        for entry in entries:
            if entry["decision"] == "skip_valid":
                continue
            out = ROOT / entry["output_directory"]
            validated = read(out / "validation-final/validation.json")
            package_file = out / "package-reviewed.json"
            if validated["package_file_sha256"] != digest(package_file):
                raise ValueError("Package changed after isolated validation")
            imported = ok(client.post("/api/module-preparations/import", json=read(package_file)))
            repeated = ok(client.post("/api/module-preparations/import", json=read(package_file)))
            assert repeated["reused"] and repeated["preparation_id"] == imported["preparation_id"]
            assert imported["preparation"]["package_bindable"]
            prior_path = out / "publication.json"
            history_path = out / "publication-history.json"
            if prior_path.exists():
                previous = read(prior_path)
                history = read(history_path) if history_path.exists() else []
                if not any(
                    p["import"]["preparation_id"] == previous["import"]["preparation_id"]
                    for p in history
                ):
                    write(history_path, [*history, previous])
            write(
                out / "publication.json",
                {
                    "database_url": settings.database_url,
                    "import": imported,
                    "repeat_import_reused": True,
                    "existing_room_bindings_changed": False,
                },
            )
            results[entry["module_id"]] = {
                "id": imported["preparation_id"],
                "version": imported["preparation"]["version"],
            }
    return results
