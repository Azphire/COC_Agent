"""Read-only capture of actual persisted batch-48 evidence."""

import argparse
import json
import sqlite3
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parents[2] / "data/prepared/batch-48/real-01"


def rows(db, table):
    cursor = db.execute(f'SELECT * FROM "{table}"')
    names = [c[0] for c in cursor.description]
    values = []
    for row in cursor:
        value = dict(zip(names, row, strict=True))
        for key in (
            "document",
            "payload",
            "state",
            "session_state",
            "steps",
            "character_snapshot",
            "public_summary",
        ):
            if isinstance(value.get(key), str):
                try:
                    value[key] = json.loads(value[key])
                except ValueError:
                    pass
        values.append(value)
    return values


def capture(name):
    path = DIRECTORY / name
    if path.exists():
        raise ValueError("Refusing to overwrite original evidence")
    with sqlite3.connect(f"file:{(DIRECTORY / 'game.db').as_posix()}?mode=ro", uri=True) as db:
        data = {
            table: rows(db, table)
            for table in (
                "party_generation_batches",
                "launch_drafts",
                "game_rooms",
                "room_members",
                "room_character_slots",
                "room_events",
                "agent_profiles",
                "agent_model_calls",
                "agent_cycles",
                "agent_runs",
                "action_plan_records",
            )
            if db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone()
        }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print({key: len(value) for key, value in data.items()})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("name")
    capture(parser.parse_args().name)
