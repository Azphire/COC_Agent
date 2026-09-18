"""Read-only baseline evidence and protected-file inventory for batch 37."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "data/prepared/changan/batch-37"
SOURCE = ROOT / "data/prepared/changan/batch-36/run-20260917T235205Z"


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    BASE.mkdir(parents=True, exist_ok=True)
    protected = BASE / "protected-files-initial.json"
    if not protected.exists():
        paths = [ROOT / ".env", *list((ROOT / "data").rglob("*"))]
        write(
            protected,
            {
                str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in paths
                if p.is_file() and not p.is_relative_to(BASE)
            },
        )
    private = SOURCE / "private-audit"

    def load(name):
        return json.loads((private / (name + ".json")).read_text(encoding="utf-8"))

    cycles, runs, calls, events = [
        load(n) for n in ("cycles", "agent-runs", "model-calls", "host-events")
    ]
    wanted = {26, 58, 112, 127, 209, 233, 288, 319, 348, 378, 438, 462}
    evidence = []
    for cycle in cycles:
        seq = cycle["state"]["triggering_event_seq"]
        if seq not in wanted:
            continue
        selected = [r for r in runs if r["cycle_id"] == cycle["id"]]
        runids = {r["id"] for r in selected}
        trace = {
            "trigger": next(e for e in events if e["seq"] == seq),
            "cycle": cycle,
            "runs": selected,
            "calls": [c for c in calls if c["run_id"] in runids],
            "events": [e for e in events if e["payload"].get("cycle_id") == cycle["id"]],
        }
        evidence.append(trace)
        print("\nTRIGGER", seq, trace["trigger"]["payload"].get("text"))
        for run in selected:
            if run["graph_node"] not in {
                "plan_keeper_action",
                "repair_keeper_plan",
                "generate_keeper_narration",
            }:
                continue
            output = run.get("structured_output") or {}
            print(run["graph_node"], run["status"], run.get("safe_error"))
            print(
                json.dumps(
                    {
                        k: output[k]
                        for k in (
                            "parsed_intent",
                            "focus",
                            "proposed_transition_id",
                            "proposed_reveal_entity_ids",
                            "public_narration",
                            "npc_speech",
                        )
                        if k in output
                    },
                    ensure_ascii=False,
                )
            )
            for call in trace["calls"]:
                if call["run_id"] != run["id"]:
                    continue
                d = call["document"]
                print(
                    "CALL",
                    d.get("phase"),
                    d.get("error_category"),
                    d.get("safe_error"),
                    "keys",
                    list(d),
                )
                if run["graph_node"] == "repair_keeper_plan":
                    print("REPAIR INPUT", str(d.get("input_messages"))[-6000:])
    write(BASE / "baseline-evidence.json", evidence)


if __name__ == "__main__":
    main()
