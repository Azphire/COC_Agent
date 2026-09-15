"""Copy a historical save with provenance; apply only the declared package diff.

Run before starting the isolated server. Never alters historical DBs, dice,
events, character values, inventory or navigation. Restoration remains the
normal /load endpoint. This is a short reproduction, not a new full game.
"""

import argparse
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

from module_package import ROOT, read, write

from app.preparation.schemas import EntityFields


def clone(source, output):
    source, output = source.resolve(), output.resolve()
    assert source.is_relative_to(ROOT / "data/prepared/changan")
    assert output.is_relative_to(ROOT / "data/prepared/changan/batch-21")
    assert not output.exists(), "Never overwrite a previous run"
    output.mkdir(parents=True)
    hashes = {}
    for name in ["game.db", "knowledge.db", "checkpoint.db"]:
        path = source / name
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        with (
            sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as src,
            sqlite3.connect(output / name) as dst,
        ):
            src.backup(dst)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == hashes[name]
    for name in [
        "session.json",
        "load-info.json",
        "save.json",
        "scenario.json",
        "before-restart.json",
        "before-restart-checks.json",
    ]:
        if (source / name).exists():
            shutil.copyfile(source / name, output / name)
    package = read(output.parent / "package-approved.json")
    mapping = read(output / "load-info.json")["entity_ids"]

    def translate(v):
        if isinstance(v, str):
            return mapping.get(v, v)
        if isinstance(v, list):
            return [translate(x) for x in v]
        if isinstance(v, dict):
            return {k: translate(x) for k, x in v.items()}
        return v

    changes = []
    with sqlite3.connect(output / "game.db") as db:
        for key in ["newspaper", "note_front"]:
            eid = mapping[key]
            fields = EntityFields.model_validate(
                next(e["fields"] for e in package["entities"] if e["key"] == key)
            ).model_dump(mode="json")
            for room_id, snapshot in db.execute(
                "SELECT room_id,snapshot FROM room_entity_states WHERE source_entity_id=?", (eid,)
            ).fetchall():
                old = json.loads(snapshot)
                new = {**old, **translate(fields)}
                changes.append(dict(key=key, entity_id=eid, before=old, after=new))
                db.execute(
                    "UPDATE room_entity_states SET snapshot=?,entity_type=? "
                    "WHERE room_id=? AND source_entity_id=?",
                    (json.dumps(new, ensure_ascii=False), fields["type"], room_id, eid),
                )
    write(
        output / "fixture-provenance.json",
        dict(
            kind="historical_save_copy_with_declared_package_update",
            source=str(source.relative_to(ROOT)),
            source_db_sha256=hashes,
            package_sha256=hashlib.sha256(
                (output.parent / "package-approved.json").read_bytes()
            ).hexdigest(),
            changes=changes,
            dice_or_world_state_modified=False,
        ),
    )
    write(output / "package.json", package)
    print("Created isolated copy", output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    clone(args.source, args.output)
