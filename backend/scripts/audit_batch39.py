"""Delivery checks over the explicit protection list and existing batch exports."""

import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "data/prepared/changan/batch-39"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def delivery_manifest(directory):
    """Index reviewable exports and original inputs, excluding live credentials."""
    operations = directory / "operations.jsonl"
    public = directory / "public-events.json"
    if operations.exists() and public.exists():
        submitted = {e.get("client_request_id"): e["seq"] for e in load(public)
                     if e["type"] == "action.submitted"}
        inputs = []
        for line in operations.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("method") == "POST" and row.get("path", "").endswith("/actions"):
                body = row.get("body") or {}
                inputs.append({
                    "time": row["time"], "text": body.get("text"),
                    "client_request_id": body.get("client_request_id"),
                    "status": row["status"],
                    "public_event_seq": submitted.get(body.get("client_request_id")),
                })
        write(directory / "player-inputs.json", inputs)
    manifest = directory / "audit-manifest.json"
    paths = {item["path"].replace("\\", "/") for item in load(manifest)}
    paths.update(name for name in (
        "public-events.json", "player-inputs.json", "code-version.json", "code.diff",
        "quality-notes.md", "restore-verification.json", "medical-restore-verification.json",
        "run-classification.json", "application-version-verification.json",
        "fixture-provenance.json", "postgame-review.json", "context-review.json",
        "model-input-review.json",
    ) if (directory / name).exists())
    write(manifest, [{"path": name,
                      "sha256": hashlib.sha256((directory / name).read_bytes()).hexdigest()}
                     for name in sorted(paths)])


def delivery_secrets(directory):
    """Compare exports with this isolated run's credentials without printing them."""
    secrets = set()

    def collect(value, credential_file=False):
        if isinstance(value, dict):
            for key, item in value.items():
                if (credential_file or "token" in key.lower()) \
                        and isinstance(item, str) and len(item) > 16:
                    secrets.add(item)
                else:
                    collect(item, credential_file)
        elif isinstance(value, list):
            for item in value:
                collect(item, credential_file)

    for name in ("private-auth.json", "session.json"):
        if (directory / name).exists():
            collect(load(directory / name), name == "private-auth.json")
    paths = {item["path"] for item in load(directory / "audit-manifest.json")}
    paths.update(name for name in ("code-version.json", "code.diff", "quality-notes.md",
                                   "restore-verification.json") if (directory / name).exists())
    leaked = [name for name in sorted(paths)
              if any(secret in (directory / name).read_text(encoding="utf-8")
                     for secret in secrets)]
    return {"credential_values_checked": len(secrets), "export_files_checked": len(paths),
            "credential_matches": leaked}


def main():
    initial = load(BASE / "protected-files-initial.json")
    final = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
             if (ROOT / name).is_file() else None for name in initial}
    changed = [name for name, digest in initial.items() if final[name] != digest]
    write(BASE / "protected-files-final.json", final)
    write(BASE / "protection-verification.json", {
        "checked": len(initial), "changed_or_missing": changed,
        "root_data_files": {str(folder.relative_to(ROOT)): sorted(p.name for p in folder.iterdir()
                            if p.is_file()) for folder in (ROOT / "data", ROOT / "backend/data")
                            if folder.exists()},
        "definition": "Only the initial explicit list is hashed; no historical tree rescan.",
    })
    rows = []
    for metrics_path in sorted(BASE.glob("**/interaction-metrics.json")):
        directory = metrics_path.parent
        delivery_manifest(directory)
        metrics = load(metrics_path)
        calls = load(directory / "private-audit/model-calls.json")
        version = load(directory / "code-version.json")
        provenance = directory / "fixture-provenance.json"
        initial_max_seq = load(provenance).get("initial_max_seq", 0) if provenance.exists() else 0
        rows.append({
            "directory": str(directory.relative_to(BASE)),
            **{k: v for k, v in metrics.items() if k not in {"cycle_review", "fallback_details"}},
            "model_failure_categories": dict(Counter(c["document"].get("error_category")
                                                     for c in calls
                                                     if c["document"].get("error_category"))),
            "timing": load(directory / "timing-summary.json"),
            "delivery_secret_check": delivery_secrets(directory),
            "teammate_confirmed_result_replies": sum(
                bool(e["payload"].get("result_source_event_seqs"))
                for e in load(directory / "private-audit/host-events.json")
                if e["type"] == "agent.spoke" and e["seq"] > initial_max_seq
            ),
            "application_files_changed_since_start": [p for p, digest in version["files"].items()
                if p.replace("\\", "/").startswith("backend/app/")
                and hashlib.sha256((ROOT / p).read_bytes()).hexdigest() != digest],
        })
    write(BASE / "delivery-metrics.json", rows)
    print(json.dumps({"protected": len(initial), "changed_or_missing": changed,
                      "runs": [r["directory"] for r in rows]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
