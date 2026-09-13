"""Portable local preparation packages. Original prose stays under data/prepared/.

python scripts/module_package.py audit --bundle ../data/prepared/changan/batch-16/package.json
python scripts/module_package.py load --bundle PATH --directory ../data/prepared/changan/loaded
"""

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.knowledge.schemas import KnowledgeSource  # noqa: E402
from app.module_ir.parser import validate_source  # noqa: E402
from app.module_ir.schemas import ModuleDocumentIR  # noqa: E402
from app.preparation.schemas import EntityFields  # noqa: E402

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
    source = KnowledgeSource.model_validate(package["knowledge"]["source"])
    ir = ModuleDocumentIR.model_validate(package["ir"])
    validate_source(source, ROOT / "data")
    assert source.source_hash == ir.source_hash and source.source_id == ir.source_id
    assert len(package["knowledge"]["chunks"]) == source.chunk_count
    entities = {e["key"]: EntityFields.model_validate(e["fields"]) for e in package["entities"]}
    assert len(entities) == len(package["entities"])
    nodes = {n.node_id for n in ir.nodes}
    blocks = {b.block_id for b in ir.blocks}
    scene_nodes = {
        n["node_id"] for n in package["nodes"] if n["patch"].get("approved_type") == "scene"
    }
    for e in package["entities"]:
        assert set(e["node_ids"]) <= scene_nodes and e["node_ids"]
        fields = entities[e["key"]]
        assert fields.source_block_ids and set(fields.source_block_ids) <= blocks
        assert fields.reviewed_by == package["reviewer"]
        for r in fields.interactions:
            assert set(r.source_block_ids) <= set(fields.source_block_ids)
            assert {
                *r.required_entity_ids,
                *r.required_item_ids,
                *r.acquire_item_ids,
                *r.reveal_entity_ids,
                *r.following_npc_ids,
                *(s.entity_id for s in r.required_sanity),
                *([r.item_id] if r.item_id else []),
                *([r.sound_item_id] if r.sound_item_id else []),
                *([r.npc_id] if r.npc_id else []),
                *([r.door_id] if r.door_id else []),
                *([r.observation_entity_id] if r.observation_entity_id else []),
            } <= entities.keys()
            if r.observation_effect_id:
                observed = entities[r.observation_entity_id or e["key"]]
                assert r.observation_effect_id in {effect.id for effect in observed.sanity_effects}
            assert set(r.scene_node_ids) <= scene_nodes
            if r.failure_interaction_id:
                assert r.failure_interaction_id in {other.id for other in fields.interactions}
            for s in r.required_sanity:
                assert s.effect_id in {effect.id for effect in entities[s.entity_id].sanity_effects}
    covered = set()
    for entry in package["coverage"]:
        assert set(entry["entity_keys"]) <= entities.keys()
        assert set(entry["node_ids"]) <= nodes
        assert set(entry["source_orders"]) <= {b.source_order for b in ir.blocks}
        covered.update(entry["source_orders"])
        assert entry["status"] in {"prepared", "context", "blocked", "host_ruling"}
        assert entry["status"] not in {"blocked", "host_ruling"} or entry["gaps"]
    assert covered == {b.source_order for b in ir.blocks}, "unaccounted source blocks"
    edges = package["transitions"]
    assert all(
        t["source_scene_node_id"] in scene_nodes and t["target_scene_node_id"] in scene_nodes
        for t in edges
    )
    initial = package["initial_node_id"]
    reached = {initial}
    while True:
        added = {t["target_scene_node_id"] for t in edges if t["source_scene_node_id"] in reached}
        if added <= reached:
            break
        reached |= added
    assert reached == scene_nodes, "disconnected playable scenes"
    # Potential-state closure checks for prerequisites behind their own locked
    # exit. Dice and explicit KP rulings remain alternatives, never assumed wins.
    potential_scenes, items, facts, possible_flags = {initial}, set(), set(), set()
    potential_outcomes = set()
    for _ in range(len(entities) + len(edges) + 1):
        before = (len(potential_scenes), len(items), len(facts), len(possible_flags))
        local = {e["key"] for e in package["entities"] if set(e["node_ids"]) & potential_scenes}
        facts |= local

        def ready(rule):
            return (
                set(rule.get("required_item_ids", [])) <= items
                and set(rule.get("required_entity_ids", [])) <= facts
                and all(
                    (k, v) in possible_flags or not v
                    for k, v in rule.get("required_flags", {}).items()
                )
            )

        for key in local | items:
            for rule in entities[key].interactions:
                r = rule.model_dump()
                if ready(r):
                    items.update(rule.acquire_item_ids)
                    facts.update(rule.reveal_entity_ids)
                    possible_flags.update(rule.set_flags.items())
                    if rule.outcome:
                        potential_outcomes.add(rule.outcome)
        for t in edges:
            if t["source_scene_node_id"] in potential_scenes and ready(t):
                potential_scenes.add(t["target_scene_node_id"])
        if before == (len(potential_scenes), len(items), len(facts), len(possible_flags)):
            break
    assert potential_scenes == scene_nodes, "conditional scene prerequisite cycle"
    flags = {k for e in entities.values() for r in e.interactions for k in r.set_flags}
    for t in edges:
        assert set(t.get("required_flags", {})) <= flags
        assert set(t.get("required_item_ids", [])) <= entities.keys()
    outcomes = {r.outcome for e in entities.values() for r in e.interactions if r.outcome}
    assert outcomes == potential_outcomes, "ending prerequisite cycle"
    missing = {
        key: e.combat_template.missing()
        for key, e in entities.items()
        if e.combat_template and e.combat_template.missing()
    }
    operations = {
        key: {operation: entities[key].combat_template.missing(operation) for operation in required}
        for key, required in package.get("required_npc_operations", {}).items()
    }
    operation_gaps = {
        key: {op: fields for op, fields in checks.items() if fields}
        for key, checks in operations.items()
        if any(checks.values())
    }
    return {
        "source_hash": source.source_hash,
        "file_hashes": [f["sha256"] for f in source.files],
        "pages": source.page_count,
        "blocks_accounted": len(covered),
        "nodes": len(nodes),
        "playable_scenes": len(scene_nodes),
        "entities": len(entities),
        "entity_types": dict(Counter(e.type for e in entities.values())),
        "transitions": len(edges),
        "outcomes": sorted(outcomes),
        "coverage_entries": len(package["coverage"]),
        "coverage_status": dict(Counter(e["status"] for e in package["coverage"])),
        "missing_combat_values": missing,
        "required_npc_operations": operations,
        "operation_gaps": operation_gaps,
        "reviewer": package["reviewer"],
        "human_review": False,
        "source_accounting_complete": True,
        "runtime_complete": not missing and not any(e["gaps"] for e in package["coverage"]),
        "potentially_reachable_outcomes": sorted(potential_outcomes),
        "reachability_basis": "source bindings/prerequisites; dice/KP rulings still required",
    }


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
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.persistence.preparation_models import ModulePreparation

    supplement = package.get("numeric_supplement", {})
    if supplement and not supplement.get("user_approved"):
        if not allow_unapproved_test_values:
            raise ValueError(
                "Unapproved NPC supplement: pass --allow-unapproved-test-values "
                "only for an isolated test"
            )
        target = Path(directory).resolve()
        isolated_root = (ROOT / "data/prepared/changan/batch-17").resolve()
        if not target.is_relative_to(isolated_root):
            raise ValueError(
                "Unapproved test values are restricted to data/prepared/changan/batch-17/"
            )
    summary = audit(package)
    digest = hashlib.sha256(
        json.dumps(package, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    settings = settings_for(directory)
    application = create_app(settings)
    with TestClient(
        application, headers={"Authorization": "Bearer local-package-review"}
    ) as client:
        svc = application.state.agent_service
        svc.knowledge.repository.initialize()
        knowledge = package["knowledge"]
        svc.knowledge.repository.store(
            KnowledgeSource.model_validate(knowledge["source"]), knowledge["chunks"]
        )
        svc.structure.repository.store(ModuleDocumentIR.model_validate(package["ir"]))

        def request(method, path, body=None):
            response = client.request(method, "/api" + path, json=body)
            if response.status_code >= 400:
                raise ValueError(f"{method} {path}: {response.status_code} {response.text[:1500]}")
            return response.json()

        existing = request("GET", "/module-preparations")
        prep = next((p for p in existing if p.get("package_sha256") == digest), None)
        if prep and prep["status"] == "approved":
            old = request("GET", f"/module-preparations/{prep['id']}/entities")
            structure = request("GET", f"/module-preparations/{prep['id']}/structure")
            info = {
                "preparation_id": prep["id"],
                "snapshot_id": structure["snapshot_id"],
                "package_sha256": digest,
                "reused": True,
                "entity_ids": {
                    tag.removeprefix("package:"): e["id"]
                    for e in old
                    for tag in e.get("tags", [])
                    if tag.startswith("package:")
                },
                "coverage": summary,
                "structure": structure,
            }
            write(Path(directory) / "load-info.json", info)
            coverage_detail(package, info, directory)
            return info
        if not prep:
            prep = request(
                "POST",
                "/module-preparations",
                {
                    "source_id": summary["source_hash"] and knowledge["source"]["source_id"],
                    "source_hash": summary["source_hash"],
                    "display_title": package["title"],
                },
            )

            async def mark():
                async with svc.rooms.transaction() as session:
                    row = await session.get(ModulePreparation, prep["id"])
                    row.document = {
                        **row.document,
                        "package_sha256": digest,
                        "coverage_summary": summary,
                        "reviewed_by": package["reviewer"],
                    }

            client.portal.call(mark)
        prefix = f"/module-preparations/{prep['id']}"
        prior_entities = request("GET", prefix + "/entities")
        mapping = {}
        for entry in package["entities"]:
            prior = next(
                (e for e in prior_entities if "package:" + entry["key"] in e["tags"]), None
            )
            if prior:
                mapping[entry["key"]] = prior["id"]
            else:
                created = request(
                    "POST",
                    "/module-entities",
                    {
                        **entry["fields"],
                        "preparation_id": prep["id"],
                        "tags": [*entry["fields"].get("tags", []), "package:" + entry["key"]],
                    },
                )
                mapping[entry["key"]] = created["id"]

        def translate(value):
            if isinstance(value, str):
                return mapping.get(value, value)
            if isinstance(value, list):
                return [translate(v) for v in value]
            if isinstance(value, dict):
                return {k: translate(v) for k, v in value.items()}
            return value

        for entry in package["entities"]:
            eid = mapping[entry["key"]]
            prior = next((e for e in prior_entities if e["id"] == eid), None)
            if prior and prior["status"] == "approved":
                continue  # A restart never overwrites already reviewed fields.
            fields = translate(entry["fields"])
            from app.preparation.schemas import EntityPatch

            request(
                "PATCH",
                f"/module-entities/{eid}",
                {k: v for k, v in fields.items() if k in EntityPatch.model_fields},
            )
            request("POST", f"/module-entities/{eid}/approve")
        request(
            "PATCH",
            prefix,
            {
                "initial_scene_entity_id": mapping[package["initial_entity_key"]],
                "required_entity_ids": [mapping[k] for k in package["required_entity_keys"]],
            },
        )
        request("POST", prefix + "/structure/build")
        for node in package["nodes"]:
            request("PATCH", prefix + "/structure/nodes/" + node["node_id"], node["patch"])
        for entry in package["entities"]:
            for node_id in entry["node_ids"]:
                request(
                    "POST",
                    prefix + "/structure/entity-bindings",
                    {
                        "entity_id": mapping[entry["key"]],
                        "node_id": node_id,
                        "source_hash": summary["source_hash"],
                    },
                )
        old_transitions = request("GET", prefix + "/structure/transitions")
        for transition in package["transitions"]:
            if any(
                t["source_scene_node_id"] == transition["source_scene_node_id"]
                and t["target_scene_node_id"] == transition["target_scene_node_id"]
                for t in old_transitions
            ):
                continue
            request("POST", prefix + "/structure/transitions", translate(transition))
        request("POST", prefix + "/approve")
        snapshot = request("POST", prefix + "/structure/approve", {"incomplete": False})
        info = {
            "preparation_id": prep["id"],
            "package_sha256": digest,
            "snapshot_id": snapshot["snapshot_id"],
            "entity_ids": mapping,
            "coverage": summary,
            "reused": False,
        }
        write(Path(directory) / "load-info.json", info)
        coverage_detail(package, info, directory)
        write(Path(directory) / "package.json", package)
        return info


def export(package, directory, destination):
    """Read the actual approved preparation; no room state, credentials or source rewrite."""
    from app.module_ir.schemas import NodePatch

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
        if isinstance(value, str):
            return keys.get(value, value)
        if isinstance(value, list):
            return [translate(v) for v in value]
        if isinstance(value, dict):
            return {k: translate(v) for k, v in value.items()}
        return value

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
