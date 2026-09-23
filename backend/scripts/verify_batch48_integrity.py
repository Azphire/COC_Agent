"""Read-only source protection and original-row preservation checks for real-01."""

import hashlib
import json
import sqlite3
from pathlib import Path

from validate_batch48 import DIRECTORY, SOURCE


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def main():
    target = DIRECTORY / "final-integrity.json"
    if target.exists():
        raise ValueError("Refusing to overwrite evidence")
    before = json.loads((DIRECTORY / "source-protection-before.json").read_text("utf-8"))
    protection = [
        {"path": name, "before": expected, "after": sha(Path(name))}
        for name, expected in before.items()
    ]
    tables = [
        "character_drafts",
        "character_roll_records",
        "character_events",
        "agent_profiles",
        "game_rooms",
        "room_members",
        "room_character_slots",
        "room_snapshots",
        "room_events",
        "module_preparations",
        "module_entities",
        "module_entity_relations",
    ]
    original_rows = []
    with (
        sqlite3.connect(f"file:{(SOURCE / 'game.db').as_posix()}?mode=ro", uri=True) as source,
        sqlite3.connect(f"file:{(DIRECTORY / 'game.db').as_posix()}?mode=ro", uri=True) as current,
    ):
        for table in tables:
            columns = [row[1] for row in source.execute(f'PRAGMA table_info("{table}")')]
            fields = ",".join(f'"{column}"' for column in columns)
            old = list(source.execute(f'SELECT {fields} FROM "{table}"'))
            now = set(current.execute(f'SELECT {fields} FROM "{table}"'))
            original_rows.append(
                {
                    "table": table,
                    "original_rows": len(old),
                    "all_original_rows_unchanged": all(row in now for row in old),
                }
            )
        room_id = current.execute("SELECT room_id FROM launch_drafts").fetchone()[0]
        opening = current.execute(
            "SELECT seq,payload FROM room_events WHERE room_id=? AND type='keeper.narration'",
            (room_id,),
        ).fetchall()
        opening = [seq for seq, payload in opening if json.loads(payload).get("opening")]
        counts = {
            table: current.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("launch_drafts", "party_generation_batches", "game_rooms")
        }
    profile = json.loads((DIRECTORY / "after-start.json").read_text("utf-8"))
    initial = json.loads((DIRECTORY / "before-reroll-complete.json").read_text("utf-8"))
    adopted_batch = profile["party_generation_batches"][0]["document"]
    initial_batch = initial["party_generation_batches"][0]["document"]
    observer = json.loads((DIRECTORY / "host/short-game-final.json").read_text("utf-8"))
    snapshots = [
        frame["data"]
        for frame in observer["evidence"]["frames"]
        if frame["type"] == "room.snapshot"
    ]
    result = {
        "source_files": protection,
        "all_protected_files_unchanged": all(v["before"] == v["after"] for v in protection),
        "original_records": original_rows,
        "all_original_records_unchanged": all(
            v["all_original_rows_unchanged"] for v in original_rows
        ),
        "room_id": room_id,
        "counts": counts,
        "opening_sequences": opening,
        "other_member_unchanged_after_reroll_and_adopt": (
            initial_batch["members"][1] == adopted_batch["members"][1]
        ),
        "player_snapshot_count": len(snapshots),
        "all_captured_game_snapshots_are_player_projection": all(
            not s.get("is_host", True) for s in snapshots
        ),
        "note": "No physical second-device/network claim; no private HO exists in this scenario.",
    }
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                key: value
                for key, value in result.items()
                if key not in {"source_files", "original_records"}
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
