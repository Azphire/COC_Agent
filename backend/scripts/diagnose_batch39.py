"""Bounded, read-only reuse of batch 38 evidence and its explicit protection list."""

import hashlib
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "data/prepared/changan/batch-39"
SOURCE = ROOT / "data/prepared/changan/batch-38/acceptance-frozen-20260918"


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    BASE.mkdir(parents=True, exist_ok=True)
    initial = BASE / "protected-files-initial.json"
    if not initial.exists():
        names = json.loads((SOURCE.parent / "protected-files-initial.json").read_text("utf-8"))
        paths = {ROOT / p for p in names}
        paths.update(p for p in SOURCE.iterdir() if p.is_file())
        paths.update(p for p in (SOURCE / "private-audit").iterdir() if p.is_file())
        paths.update(p for p in (ROOT / "data").iterdir() if p.is_file())
        write(initial, {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in sorted(paths) if p.is_file()})
    private = SOURCE / "private-audit"
    events, cycles, runs, calls = [json.loads((private / (n + ".json")).read_text("utf-8"))
                                  for n in ("host-events", "cycles", "agent-runs", "model-calls")]
    wanted = {125, 198, 230, 285, 303, 493, 517, 576, 608, 933, 972, 1061}
    selected = [c for c in cycles if c["state"].get("triggering_event_seq") in wanted]
    ids = {c["id"] for c in selected}
    selected_runs = [r for r in runs if r["cycle_id"] in ids]
    runids = {r["id"] for r in selected_runs}
    evidence = {"source": str(SOURCE.relative_to(ROOT)), "cycles": selected,
                "events": [e for e in events if e["seq"] in wanted
                           or e["payload"].get("cycle_id") in ids],
                "runs": selected_runs, "calls": [c for c in calls if c["run_id"] in runids]}
    write(BASE / "baseline-evidence.json", evidence)
    for e in evidence["events"]:
        if e["type"] in {"action.submitted", "combat.resolved", "module.interaction"}:
            print(e["seq"], e["type"], json.dumps(e["payload"], ensure_ascii=False)[:2400])
    with sqlite3.connect(SOURCE.joinpath("game.db").as_uri() + "?mode=ro", uri=True) as db:
        print("snapshot tables", db.execute(
            "select name from sqlite_master where type='table' and name like '%snapshot%'"
        ).fetchall())


if __name__ == "__main__":
    main()
