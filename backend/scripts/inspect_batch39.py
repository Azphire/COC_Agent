"""Read bounded public progress or sanitized run failures from an isolated probe."""

import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory")
    parser.add_argument("--after", type=int)
    parser.add_argument("--failures", action="store_true")
    parser.add_argument("--snapshots", action="store_true")
    args = parser.parse_args()
    path = ROOT / "data/prepared/changan" / args.directory
    provenance = path / "fixture-provenance.json"
    after = args.after if args.after is not None else (
        json.loads(provenance.read_text("utf-8"))["initial_max_seq"] if provenance.exists() else 0
    )
    with sqlite3.connect((path / "game.db").as_uri() + "?mode=ro", uri=True) as db:
        if args.snapshots:
            print(db.execute("select id,name,event_seq from room_snapshots").fetchall())
            return
        events = db.execute("select seq,type,payload from room_events where seq>? order by seq",
                            (after,)).fetchall()
        for seq, kind, value in events:
            p = json.loads(value)
            if kind in {"action.submitted", "keeper.narration", "agent.spoke", "npc.spoke",
                        "agent.action_proposed", "module.interaction", "combat.resolved",
                        "module.completed", "check.resolved", "action.clarification_requested"}:
                print(seq, kind, p.get("text") or p.get("summary")
                      or p.get("display_text") or p.get("question") or p)
            if args.failures and kind in {"agent.teammate_decision", "agent.narration_validated",
                                         "agent.action_validated", "module.ruling_rejected"}:
                print(seq, kind, json.dumps(p, ensure_ascii=False)[:3500])
        for status, state in db.execute(
            "select status,state from agent_cycles order by created_at desc limit 1"
        ):
            state = json.loads(state)
            print("CYCLE", status, state.get("current_node"), state.get("safe_error"))


if __name__ == "__main__":
    main()
