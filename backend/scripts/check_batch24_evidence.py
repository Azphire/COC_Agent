"""Read-only batch 24 evidence audit; no server, inference, mutation or dice replay."""

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.preparation.packages import translate_entity_refs  # noqa: E402
from app.preparation.schemas import EntityFields  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
BATCH = ROOT / "data/prepared/changan/batch-24"


def read(path):
    return json.loads(path.read_text("utf-8-sig"))


def verify(directory, san_directory):
    result = read(directory / "result.json")
    room = read(directory / "final-room.json")
    package = read(ROOT / "data/prepared/changan/batch-21/package-approved.json")
    protected = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected
        for name, expected in read(BATCH / "protected-hashes.json").items()
    }
    assert all(protected.values()), "Protected original changed"
    with sqlite3.connect((directory / "game.db").as_uri() + "?mode=ro", uri=True) as db:
        prep = json.loads(
            db.execute(
                "SELECT document FROM module_preparations WHERE id=?",
                (result["preparation_id"],),
            ).fetchone()[0]
        )
        mapping = prep["package_entity_ids"]
        for entry in package["entities"]:
            status, raw = db.execute(
                "SELECT status,document FROM module_entities WHERE id=?", (mapping[entry["key"]],)
            ).fetchone()
            assert status == "approved"
            actual = json.loads(raw)
            expected = EntityFields.model_validate(
                translate_entity_refs(entry["fields"], mapping)
            ).model_dump(mode="json")
            expected["tags"].append("package:" + entry["key"])
            assert all(actual.get(k) == v for k, v in expected.items()), entry["key"]
        assert prep["numeric_supplement"] == package["numeric_supplement"]
        calls = [
            json.loads(r[0])
            for r in db.execute("SELECT document FROM agent_model_calls ORDER BY rowid")
        ]
        events = [
            {"seq": seq, "type": kind, "payload": json.loads(raw)}
            for seq, kind, raw in db.execute(
                "SELECT seq,type,payload FROM room_events WHERE room_id=? ORDER BY seq",
                (room["id"],),
            )
        ]
    assert calls == read(directory / "model-calls.json")
    assert room["game"]["preparation"]["id"] == result["preparation_id"]
    assert result["start_ui"] and result["import_ui"]["repeat_reused"]
    assert result["import_ui"]["refresh_persisted"]
    assert all(s["http"] and s["websocket"] and s["exit_code"] == 0 for s in result["launcher"])
    kinds = Counter(e["type"] for e in events)
    san = read(san_directory / "san-fixture.json")
    assert san["status"] == "completed" and all(san["restore"].values())
    assert san["main_room_unchanged"]
    check = san["settled_check"]
    approved_effect = next(
        effect
        for e in package["entities"]
        if e["key"] == "newspaper_date"
        for effect in EntityFields.model_validate(e["fields"]).model_dump(mode="json")[
            "sanity_effects"
        ]
        if effect["id"] == "future_news"
    )
    assert check["sanity"]["effect"] == approved_effect
    assert check["sanity"]["stage"] == "done"
    assert len(san["checks"]) == 1
    restored_check = san["checks"][0]
    assert all(restored_check[k] == check[k] for k in ("id", "status", "dice", "sanity"))
    assert all(restored_check["result"][k] == v for k, v in check["result"].items())
    assert check["dice"]["roll_record"]["source"] == "system"
    return {
        "main_run": str(directory.relative_to(ROOT)),
        "room_id": room["id"],
        "preparation_id": result["preparation_id"],
        "protected_originals": protected,
        "imported_entity_fields_match": len(package["entities"]),
        "numeric_supplement_unchanged": True,
        "ui_start": True,
        "ui_repeat_reused": True,
        "ui_refresh_persisted": True,
        "restart_import_persisted": result.get("restart_import_persisted", False),
        "natural_inputs": kinds["action.submitted"],
        "natural_game_status": "not_run" if not calls else "inspect_game_evidence",
        "model_calls": len(calls),
        "model_schemas": dict(Counter(c["schema"] for c in calls)),
        "tokens": {
            k: sum((c.get("token_usage") or {}).get(k) or 0 for c in calls)
            for k in ("input", "output", "total")
        },
        "main_event_counts": dict(kinds),
        "san_fixture": {
            "directory": str(san_directory.relative_to(ROOT)),
            "room_id": san["room_id"],
            "natural_inputs": 0,
            "effect_unchanged": True,
            "check_id": check["id"],
            "roll_id": check["dice"]["roll_record"]["id"],
            "dice": check["dice"]["selected"],
            "value": check["value"],
            "before": check["sanity"]["before"],
            "after": check["sanity"]["after"],
            "loss": check["sanity"]["loss"],
            "restore": san["restore"],
            "main_room_unchanged": True,
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", default="live-c4", nargs="?")
    parser.add_argument("--san-run", default="live-c3")
    args = parser.parse_args()
    directories = [(BATCH / r).resolve() for r in (args.run, args.san_run)]
    assert all(p.is_relative_to(BATCH.resolve()) for p in directories)
    summary = verify(*directories)
    (directories[0] / "verified-evidence.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
