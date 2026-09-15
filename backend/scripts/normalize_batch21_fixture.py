"""Repair omitted schema defaults in the declared copy-only package update."""

import json
import sqlite3
import sys

from module_package import ROOT, read, write

from app.preparation.schemas import EntityFields


def main():
    directory = (ROOT / "data/prepared/changan/batch-21" / sys.argv[1]).resolve()
    assert directory.is_relative_to(ROOT / "data/prepared/changan/batch-21")
    provenance = read(directory / "fixture-provenance.json")
    with sqlite3.connect(directory / "game.db") as db:
        for change in provenance["changes"]:
            eid = change["entity_id"]
            for room, raw in db.execute(
                "select room_id,snapshot from room_entity_states where source_entity_id=?", (eid,)
            ).fetchall():
                value = json.loads(raw)
                fields = {k: v for k, v in value.items() if k in EntityFields.model_fields}
                value.update(EntityFields.model_validate(fields).model_dump(mode="json"))
                db.execute(
                    "update room_entity_states set snapshot=? "
                    "where room_id=? and source_entity_id=?",
                    (json.dumps(value, ensure_ascii=False), room, eid),
                )
    write(
        directory / "fixture-normalization.json",
        {
            "reason": "copy helper inserted raw EntityFields without loader defaults; "
            "caused missing reveal_conditions.scene_id",
            "scope": [c["entity_id"] for c in provenance["changes"]],
            "dice_or_world_state_modified": False,
        },
    )


if __name__ == "__main__":
    main()
