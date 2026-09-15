"""Portable local preparation packages. Original prose stays under data/prepared/.

python scripts/module_package.py audit --bundle ../data/prepared/changan/batch-16/package.json
python scripts/module_package.py load --bundle PATH --directory ../data/prepared/changan/loaded
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def coverage_detail(package, info, directory):
    blocks = {b["source_order"]: b for b in package["ir"]["blocks"]}
    write(
        Path(directory) / "coverage-audit.json",
        {
            "preparation_id": info["preparation_id"],
            "package_sha256": info["package_sha256"],
            "summary": info["coverage"],
            "entries": [
                {
                    **entry,
                    "entity_ids": [info["entity_ids"][key] for key in entry["entity_keys"]],
                    "source_locations": [
                        {k: blocks[o][k] for k in ("block_id", "source_order", "source_position")}
                        for o in entry["source_orders"]
                    ],
                }
                for entry in package["coverage"]
            ],
        },
    )


def audit(package):
    from app.preparation.packages import audit as validate_package

    return validate_package(package, ROOT / "data")


def settings_for(directory):
    from app.config import Settings

    directory = Path(directory).resolve()
    if not directory.is_relative_to(ROOT / "data/prepared"):
        raise ValueError("Only an explicit data/prepared/ directory is allowed")
    directory.mkdir(parents=True, exist_ok=True)
    return Settings(
        _env_file=None,
        host_admin_token="local-package-review",
        data_dir=ROOT / "data",
        database_url=f"sqlite+aiosqlite:///{(directory / 'game.db').as_posix()}",
        knowledge_db_path=directory / "knowledge.db",
        checkpoint_db_path=directory / "checkpoint.db",
    )


def load(package, directory, *, allow_unapproved_test_values=False):
    import asyncio

    from app.main import create_app

    # CLI isolation is explicit; the importer itself only uses the supplied application.
    if allow_unapproved_test_values:
        target = Path(directory).resolve()
        if not target.is_relative_to((ROOT / "data/prepared/changan/batch-17").resolve()):
            raise ValueError("Unapproved test values are restricted to batch-17")
    application = create_app(settings_for(directory))

    async def run():
        async with application.router.lifespan_context(application):
            return await application.state.agent_service.packages.import_package(package)

    info = asyncio.run(run())
    write(Path(directory) / "load-info.json", info)
    coverage_detail(package, info, directory)
    write(Path(directory) / "package.json", package)
    return info


def export(package, directory, destination):
    """Read the actual approved preparation; no room state, credentials or source rewrite."""
    from app.module_ir.schemas import NodePatch
    from app.preparation.packages import translate_entity_refs
    from app.preparation.schemas import EntityFields

    directory = Path(directory).resolve()
    if not directory.is_relative_to(ROOT / "data/prepared"):
        raise ValueError("Export requires an explicit independent data/prepared directory")
    info = read(directory / "load-info.json")
    with sqlite3.connect((directory / "game.db").as_uri() + "?mode=ro", uri=True) as db:
        prep = db.execute(
            "select status, document from module_preparations where id=?", (info["preparation_id"],)
        ).fetchone()
        assert prep and prep[0] == "approved"
        rows = db.execute(
            "select id, type, status, document from module_entities where preparation_id=?",
            (info["preparation_id"],),
        ).fetchall()
        structure = db.execute(
            "select document from module_structure_overrides where preparation_id=?",
            (info["preparation_id"],),
        ).fetchone()
    snapshot = json.loads(structure[0])
    assert snapshot["approved"]
    keys = {
        eid: next(
            t.removeprefix("package:")
            for t in json.loads(doc).get("tags", [])
            if t.startswith("package:")
        )
        for eid, _, _, doc in rows
    }

    def translate(value):
        return translate_entity_refs(value, keys)

    package = json.loads(json.dumps(package))
    package["entities"] = []
    for eid, kind, status, document in rows:
        assert status == "approved"
        fields = {k: v for k, v in json.loads(document).items() if k in EntityFields.model_fields}
        fields["type"] = kind
        fields["tags"] = [t for t in fields.get("tags", []) if not t.startswith("package:")]
        package["entities"].append(
            {
                "key": keys[eid],
                "fields": translate(fields),
                "node_ids": [
                    b["node_id"] for b in snapshot["entity_bindings"] if b["entity_id"] == eid
                ],
            }
        )
    package["nodes"] = [
        {
            "node_id": n["node_id"],
            "patch": {
                k: v for k, v in n.items() if k in NodePatch.model_fields and k != "parent_node_id"
            },
        }
        for n in snapshot["nodes"]
    ]
    package["transitions"] = [
        translate({k: v for k, v in t.items() if k not in {"relation_id", "transition_id"}})
        for t in snapshot["transitions"]
    ]
    for node in package["nodes"]:
        node["patch"]["initial_scene"] = node["node_id"] == snapshot["initial_scene_node_id"]
    summary = audit(package)
    write(destination, package)
    return {"path": str(destination), "coverage": summary}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["audit", "load", "export", "serve"])
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--allow-unapproved-test-values", action="store_true")
    args = parser.parse_args()
    if args.operation == "serve":
        import uvicorn

        from app.main import create_app

        assert args.directory and (args.directory / "game.db").is_file(), "Load the package first"
        uvicorn.run(create_app(settings_for(args.directory)), host="127.0.0.1", port=args.port)
        return
    if not args.bundle:
        parser.error("--bundle is required for audit/load/export")
    package = read(args.bundle)
    if args.operation == "audit":
        result = audit(package)
        if args.output:
            write(args.output, result)
    elif args.operation == "load":
        result = load(
            package,
            args.directory.resolve(),
            allow_unapproved_test_values=args.allow_unapproved_test_values,
        )
    else:
        result = export(package, args.directory, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
