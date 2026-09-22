"""Validate and import portable preparations into the current application services."""

import asyncio
import hashlib
import json
from collections import Counter
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from app.knowledge.indexer import safe_path, sha256
from app.knowledge.schemas import KnowledgeSource
from app.module_ir.parser import stable_id, validate_source
from app.module_ir.schemas import (
    ApproveStructure,
    BindingInput,
    ModuleDocumentIR,
    NodePatch,
    SceneTransition,
    StructureSnapshot,
)
from app.persistence.module_ir_models import StructureOverride
from app.persistence.preparation_models import ModuleEntity, ModulePreparation
from app.preparation.schemas import EntityFields, Scope
from app.rooms.service import require


def content_digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def translate_entity_refs(value, mapping, field=""):
    """Translate references only; titles, prose, effect IDs and numeric sources stay literal."""
    if isinstance(value, str):
        reference = field in {
            "entity_id",
            "item_id",
            "npc_id",
            "scene_id",
            "door_id",
        } or field.endswith(
            ("_entity_id", "_entity_ids", "_item_id", "_item_ids", "_npc_id", "_npc_ids")
        )
        return mapping.get(value, value) if reference else value
    if isinstance(value, list):
        return [translate_entity_refs(v, mapping, field) for v in value]
    if isinstance(value, dict):
        return {k: translate_entity_refs(v, mapping, k) for k, v in value.items()}
    return value


def evidence_registry():
    return json.loads(
        (Path(__file__).parent / "definitions/package_evidence.json").read_text("utf-8")
    )


def entity_source_references(source, ir, fields):
    """Preserve the file/page identity in mixed PDF, Word and attachment sources."""
    selected = set(fields.source_block_ids)
    references = {}
    for block in ir.blocks:
        if block.block_id not in selected:
            continue
        position = block.source_position
        extension = Path(position.file_reference).suffix.lower()
        page_kind = "pdf" if extension == ".pdf" else "word" if extension == ".doc" else "text"
        key = (position.file_reference, position.physical_page, page_kind)
        references[key] = {
            "source_id": source.source_id,
            "source_hash": source.source_hash,
            "source_title": source.title,
            "file_name": Path(position.file_reference).name,
            "file_reference": position.file_reference,
            "physical_page": position.physical_page,
            "page_kind": page_kind,
        }
    return list(references.values())


def validate_package_source(source, data_dir):
    """Use the established path boundary and give hosts a useful file-level reason."""
    root = safe_path(Path(data_dir), source.relative_reference)
    require(root.is_dir(), f"原稿目录缺失：{source.relative_reference}", 422)
    for file in source.files:
        path = safe_path(root, file["reference"])
        require(path.is_file(), f"原稿文件缺失：{file['reference']}", 422)
        require(
            sha256(path) == file["sha256"],
            f"原稿文件内容已变更：{file['reference']}（SHA256 不匹配）",
            422,
        )
    try:
        return validate_source(source, data_dir)
    except ValueError as error:
        raise ValueError("来源文件清单已变化（新增或移除文件），请重新索引并准备") from error


def numeric_review(package):
    supplement = package.get("numeric_supplement") or {}
    registry = evidence_registry()
    known = registry.get("source_numeric_values", {}).get(package["ir"]["source_hash"], {})
    changed = [
        e["key"]
        for e in package["entities"]
        if e["fields"]["type"] == "npc"
        and e["key"] in known
        and content_digest({k: e["fields"].get(k) for k in ("combat_template", "check_stats")})
        != known[e["key"]]
    ]
    unregistered = {
        e["key"]
        for e in package["entities"]
        if e["fields"]["type"] == "npc" and e["key"] not in known
        for profile in (e["fields"].get("combat_template"), e["fields"].get("check_stats"))
        if profile
        and (known or not profile["source"].startswith("module:" + package["ir"]["source_hash"]))
    }
    if not supplement:
        pending = list(
            dict.fromkeys(
                [
                    *changed,
                    *sorted(unregistered),
                    *[
                        e["key"]
                        for e in package["entities"]
                        if "user-approved:"
                        in (e["fields"].get("combat_template") or {}).get("source", "")
                    ],
                ]
            )
        )
        return {
            "status": "requires_host_review" if pending else "source_only",
            "pending_entity_keys": pending,
        }
    approval = supplement.get("approval") or {}
    approved = (
        supplement.get("user_approved") is True
        and supplement.get("status") == "approved"
        and approval.get("user_approved") is True
        and approval.get("status") == "approved"
        and approval.get("approved_proposal_id") == supplement.get("id")
        and registry["numeric_approvals"].get(approval.get("id")) == content_digest(supplement)
    )
    keys = [e["key"] for e in package["entities"] if e["key"] in supplement]
    require(keys, "数值补充没有对应实体", 422)

    def contains(actual, expected):
        if isinstance(expected, dict):
            return isinstance(actual, dict) and all(
                k in actual and contains(actual[k], v) for k, v in expected.items()
            )
        if isinstance(expected, list):
            return (
                isinstance(actual, list)
                and len(actual) == len(expected)
                and all(contains(a, e) for a, e in zip(actual, expected, strict=True))
            )
        return actual == expected

    for entry in package["entities"]:
        if entry["key"] in keys:
            expected = {k: v for k, v in supplement[entry["key"]].items() if k != "provenance"}
            require(
                contains(entry["fields"].get("combat_template"), expected),
                f"实体 {entry['key']} 的补充值与批准记录不一致；请创建新补充并审阅",
                422,
            )
    pending = set(changed) | unregistered | set(keys)
    if approved:
        pending = set(changed) | (unregistered - set(keys))
    return {
        "status": "approved" if not pending else "requires_host_review",
        "approval_id": approval.get("id"),
        "supplement_id": supplement.get("id"),
        "pending_entity_keys": sorted(pending),
    }


def audit(package, data_dir):
    """Static provenance and reference checks; no inference or gameplay assertions."""
    require(package.get("format_version") == 1, "只支持准备包 format_version=1", 422)
    summary = _audit(package, Path(data_dir))
    source = KnowledgeSource.model_validate(package["knowledge"]["source"])
    ir = ModuleDocumentIR.model_validate(package["ir"])
    require(source.kind == "module", "准备包来源必须是 module", 422)
    require(
        ir.structure_version == stable_id("ir1_", source.source_id, source.source_hash),
        "IR 版本与来源 hash 不一致",
        422,
    )
    digest = hashlib.sha256()
    for file in sorted(source.files, key=lambda f: f["reference"]):
        digest.update(
            json.dumps(
                [file["reference"], file["sha256"]], ensure_ascii=False, separators=(",", ":")
            ).encode()
        )
        digest.update(b"\n")
    require(digest.hexdigest() == source.source_hash, "来源汇总 hash 与文件清单不一致", 422)
    files = {f["reference"]: f["sha256"] for f in source.files}
    chunks = package["knowledge"]["chunks"]
    require(len({c["chunk_id"] for c in chunks}) == len(chunks), "知识块 ID 重复", 422)
    chunk_ids = {c["chunk_id"] for c in chunks}
    for c in chunks:
        require(
            c["source_id"] == source.source_id
            and c["source_hash"] == source.source_hash
            and c["file_reference"] in files
            and c["file_hash"] == files[c["file_reference"]],
            f"知识块 {c['chunk_id']} 来源引用不一致",
            422,
        )
        require(
            all(not c.get(k) or c[k] in chunk_ids for k in ("previous_id", "next_id")),
            f"知识块 {c['chunk_id']} 相邻引用不存在",
            422,
        )
    require(
        all(b.source_position.file_reference in files for b in ir.blocks),
        "原文块引用的来源文件不存在",
        422,
    )
    entries = {e["key"]: e for e in package["entities"]}
    nodes = {n.node_id for n in ir.nodes}
    require(
        len({n["node_id"] for n in package["nodes"]}) == len(package["nodes"]), "节点校正重复", 422
    )
    require({n["node_id"] for n in package["nodes"]} <= nodes, "节点校正引用不存在", 422)
    initial = entries.get(package["initial_entity_key"])
    require(
        initial
        and initial["fields"]["type"] == "scene"
        and package["initial_node_id"] in initial["node_ids"],
        "初始场景实体与节点不一致",
        422,
    )
    require(set(package["required_entity_keys"]) <= entries.keys(), "开场必要实体引用不存在", 422)
    require(
        [n["node_id"] for n in package["nodes"] if n["patch"].get("initial_scene")]
        == [package["initial_node_id"]],
        "初始节点校正必须唯一并匹配声明",
        422,
    )
    for entry in entries.values():
        fields = EntityFields.model_validate(entry["fields"])
        condition = fields.reveal_conditions
        require(
            set(condition.required_entity_ids) <= entries.keys(),
            f"实体 {entry['key']} 公开条件引用不存在",
            422,
        )
        for rule in fields.interactions:
            refs = [rule.opposed_npc_id, *[r.surviving_npc_id for r in rule.san_rewards]]
            require(
                all(not ref or ref in entries for ref in refs),
                f"交互 {entry['key']}/{rule.id} NPC 引用不存在",
                422,
            )
        for adjustment in fields.check_adjustments:
            require(
                set(adjustment.source_block_ids) <= set(fields.source_block_ids)
                and set(adjustment.scene_node_ids) <= nodes
                and set(adjustment.required_entity_ids + adjustment.forbidden_entity_ids)
                <= entries.keys(),
                "检定修正来源或场景引用不存在",
                422,
            )
    for edge in package["transitions"]:
        SceneTransition.model_validate(edge)
        require(edge.get("approved") is True, "转换尚未批准", 422)
        require(
            set(edge.get("required_revealed_entity_ids", [])) <= entries.keys(),
            "转换揭示条件引用不存在",
            422,
        )
    summary["numeric_review"] = numeric_review(package)
    summary["runtime_acceptance"] = evidence_registry()["packages"].get(
        content_digest(package),
        {
            "status": "not_run_by_import",
            "verified_scope": [],
            "not_verified": ["此内容版本没有登记实测；静态校验不代表自然游戏通过"],
        },
    )
    return summary


class PackageService:
    def __init__(self, agents):
        self.agents = agents
        self.lock = asyncio.Lock()

    async def runtime_digest(self, session, prep):
        entities = await self.agents.preparation.entities(session, prep.id)
        structure = await session.get(StructureOverride, prep.id)
        return content_digest(
            {
                "entities": [
                    [e.id, e.type, e.status, e.document]
                    for e in sorted(entities, key=lambda e: e.id)
                ],
                "structure": structure.document if structure else None,
                "initial": prep.document["initial_scene_entity_id"],
                "required": prep.document["required_entity_ids"],
            }
        )

    async def import_package(self, package, file_sha256=None):
        svc = self.agents
        # Indexing and imports share a source lock; room publication is one transaction.
        async with self.lock, svc.knowledge.index_lock:
            summary = await asyncio.to_thread(audit, package, svc.settings.data_dir)
            digest = content_digest(package)
            source = KnowledgeSource.model_validate(package["knowledge"]["source"])
            ir = ModuleDocumentIR.model_validate(package["ir"])
            svc.knowledge.repository.initialize()
            old_ir = svc.structure.repository.get(ir.structure_version)
            require(
                not old_ir or old_ir.model_dump(mode="json") == ir.model_dump(mode="json"),
                "相同来源版本的原始结构不同；不能覆盖已有原稿结构",
                422,
            )
            if not svc.knowledge.repository.available(source.source_id, source.source_hash):
                svc.knowledge.repository.store(source, package["knowledge"]["chunks"])
            if not old_ir:
                svc.structure.repository.store(ir)
            async with svc.rooms.transaction() as session:
                previous = list(
                    await session.scalars(
                        select(ModulePreparation).where(
                            ModulePreparation.source_id == source.source_id
                        )
                    )
                )
                for prep in previous:
                    if (
                        prep.document.get("package_sha256") == digest
                        and prep.status == "approved"
                        and prep.version == prep.document.get("package_import_version")
                        and prep.document.get("package_runtime_sha256")
                        == await self.runtime_digest(session, prep)
                    ):
                        structure = await svc.structure.view(session, prep.id)
                        if (
                            structure["approved_snapshot_id"]
                            and structure["approved"]
                            and not structure["stale"]
                        ):
                            return await self.info(session, prep, summary, True)
                mapping = {e["key"]: str(uuid4()) for e in package["entities"]}

                def translate(value):
                    return translate_entity_refs(value, mapping)

                version = max((p.version for p in previous), default=0) + 1
                prep = ModulePreparation(
                    id=str(uuid4()),
                    source_id=source.source_id,
                    source_hash=source.source_hash,
                    status="review_ready",
                    version=version,
                    document={
                        "display_title": package["title"],
                        "scope": Scope().model_dump(),
                        "initial_scene_entity_id": mapping[package["initial_entity_key"]],
                        "required_entity_ids": [
                            mapping[k] for k in package["required_entity_keys"]
                        ],
                        "safe_error": None,
                        "completed_batches": 0,
                        "total_batches": 0,
                        "package_sha256": digest,
                        "package_file_sha256": file_sha256,
                        "package_import_version": version,
                        "package_entity_ids": mapping,
                        "numeric_supplement": package.get("numeric_supplement"),
                        "coverage_summary": summary,
                        "reviewed_by": package["reviewer"],
                    },
                )
                # Dictionary keys are entity keys too for required operations.
                prep.document["package_required_operations"] = {
                    mapping[k]: v for k, v in package.get("required_npc_operations", {}).items()
                }
                session.add(prep)
                await session.flush()
                pending = summary["numeric_review"]["pending_entity_keys"]
                for entry in package["entities"]:
                    fields = EntityFields.model_validate(translate(entry["fields"]))
                    document = fields.model_dump(mode="json")
                    document.update(
                        tags=[*document["tags"], "package:" + entry["key"]],
                        generated_by="host",
                        host_edited=False,
                        validation_errors=[],
                        source_references=entity_source_references(source, ir, fields),
                    )
                    row = ModuleEntity(
                        id=mapping[entry["key"]],
                        preparation_id=prep.id,
                        type=fields.type,
                        status="draft" if entry["key"] in pending else "approved",
                        version=1,
                        document=document,
                    )
                    session.add(row)
                    errors = await svc.preparation.validation_errors(session, prep, row)
                    require(not errors, f"实体 {entry['key']}：{'；'.join(errors)}", 422)
                draft = StructureSnapshot(
                    snapshot_id=str(uuid4()),
                    preparation_id=prep.id,
                    preparation_version=version,
                    source_id=source.source_id,
                    source_hash=source.source_hash,
                    structure_version=ir.structure_version,
                    root_node_id=ir.root_node_id,
                    nodes=ir.nodes,
                )
                session.add(
                    StructureOverride(
                        preparation_id=prep.id, document=draft.model_dump(mode="json")
                    )
                )
                await session.flush()
                for node in package["nodes"]:
                    await svc.structure.patch_node(
                        session, prep.id, node["node_id"], NodePatch.model_validate(node["patch"])
                    )
                # Binding validation requires approved entities. For pending supplements,
                # build the same draft with those entities temporarily validated, then restore
                # draft statuses before committing; no approved snapshot is published.
                pending_rows = [await session.get(ModuleEntity, mapping[k]) for k in pending]
                for row in pending_rows:
                    row.status = "approved"
                await session.flush()
                for entry in package["entities"]:
                    for node_id in entry["node_ids"]:
                        await svc.structure.entity_binding(
                            session,
                            prep.id,
                            BindingInput(
                                entity_id=mapping[entry["key"]],
                                node_id=node_id,
                                source_hash=source.source_hash,
                            ),
                        )
                for edge in package["transitions"]:
                    await svc.structure.transition(
                        session, prep.id, SceneTransition.model_validate(translate(edge))
                    )
                for row in pending_rows:
                    row.status = "draft"
                await session.flush()
                if not pending and summary["required_operations_ready"]:
                    await svc.preparation.approve_in_session(session, prep.id)
                    await svc.structure.approve(
                        session, prep.id, ApproveStructure(incomplete=False)
                    )
                svc.preparation.activity(prep, "准备包导入；未调用模型")
                await session.flush()
                prep.document = {
                    **prep.document,
                    "package_runtime_sha256": await self.runtime_digest(session, prep),
                }
                return await self.info(session, prep, summary, False)

    async def info(self, session, prep, summary, reused):
        structure = await self.agents.structure.view(session, prep.id)
        return {
            "preparation_id": prep.id,
            "snapshot_id": structure["approved_snapshot_id"],
            "package_sha256": prep.document["package_sha256"],
            "reused": reused,
            "entity_ids": prep.document["package_entity_ids"],
            "coverage": summary,
            "preparation": await self.agents.preparation.view(session, prep),
        }


def _audit(package, data_dir):
    source = KnowledgeSource.model_validate(package["knowledge"]["source"])
    ir = ModuleDocumentIR.model_validate(package["ir"])
    validate_package_source(source, data_dir)
    if not (source.source_hash == ir.source_hash and source.source_id == ir.source_id):
        raise ValueError("来源或实体约束不一致")
    if not len(package["knowledge"]["chunks"]) == source.chunk_count:
        raise ValueError(
            "package_invalid: len(package['knowledge']['chunks']) == source.chunk_count"
        )
    entities = {e["key"]: EntityFields.model_validate(e["fields"]) for e in package["entities"]}
    if not len(entities) == len(package["entities"]):
        raise ValueError("package_invalid: len(entities) == len(package['entities'])")
    nodes = {n.node_id for n in ir.nodes}
    blocks = {b.block_id for b in ir.blocks}
    scene_nodes = {
        n["node_id"] for n in package["nodes"] if n["patch"].get("approved_type") == "scene"
    }
    for e in package["entities"]:
        if not (set(e["node_ids"]) <= scene_nodes and e["node_ids"]):
            raise ValueError("package_invalid: set(e['node_ids']) <= scene_nodes and e['node_ids']")
        fields = entities[e["key"]]
        if not (fields.source_block_ids and set(fields.source_block_ids) <= blocks):
            raise ValueError("来源或实体约束不一致")
        if not fields.reviewed_by == package["reviewer"]:
            raise ValueError("package_invalid: fields.reviewed_by == package['reviewer']")
        for r in fields.interactions:
            if not set(r.source_block_ids) <= set(fields.source_block_ids):
                raise ValueError(
                    "package_invalid: set(r.source_block_ids) <= set(fields.source_block_ids)"
                )
            if (
                not {
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
                }
                <= entities.keys()
            ):
                raise ValueError("交互引用不存在")
            if r.observation_effect_id:
                observed = entities[r.observation_entity_id or e["key"]]
                if r.observation_effect_id not in {effect.id for effect in observed.sanity_effects}:
                    raise ValueError("观察 SAN 效果引用不存在")
            if not set(r.scene_node_ids) <= scene_nodes:
                raise ValueError("package_invalid: set(r.scene_node_ids) <= scene_nodes")
            if r.failure_interaction_id:
                if r.failure_interaction_id not in {other.id for other in fields.interactions}:
                    raise ValueError("失败分支交互引用不存在")
            for s in r.required_sanity:
                if s.effect_id not in {
                    effect.id for effect in entities[s.entity_id].sanity_effects
                }:
                    raise ValueError("前置 SAN 效果引用不存在")
    covered = set()
    for entry in package["coverage"]:
        if not set(entry["entity_keys"]) <= entities.keys():
            raise ValueError("package_invalid: set(entry['entity_keys']) <= entities.keys()")
        if not set(entry["node_ids"]) <= nodes:
            raise ValueError("package_invalid: set(entry['node_ids']) <= nodes")
        if not set(entry["source_orders"]) <= {b.source_order for b in ir.blocks}:
            raise ValueError("覆盖条目引用的来源块不存在")
        covered.update(entry["source_orders"])
        if entry["status"] not in {"prepared", "context", "blocked", "host_ruling"}:
            raise ValueError("历史覆盖分类或缺值记录无效")
        if not (entry["status"] not in {"blocked", "host_ruling"} or entry["gaps"]):
            raise ValueError("历史覆盖分类或缺值记录无效")
    if not covered == {b.source_order for b in ir.blocks}:
        raise ValueError("unaccounted source blocks")
    edges = package["transitions"]
    if not all(
        (
            t["source_scene_node_id"] in scene_nodes and t["target_scene_node_id"] in scene_nodes
            for t in edges
        )
    ):
        raise ValueError("转换两端必须是批准场景")
    initial = package["initial_node_id"]
    reached = {initial}
    while True:
        added = {t["target_scene_node_id"] for t in edges if t["source_scene_node_id"] in reached}
        if added <= reached:
            break
        reached |= added
    if not reached == scene_nodes:
        raise ValueError("disconnected playable scenes")
    potential_scenes, items, facts, possible_flags = ({initial}, set(), set(), set())
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
                    (
                        (k, v) in possible_flags or not v
                        for k, v in rule.get("required_flags", {}).items()
                    )
                )
            )

        for key in local | items:
            for rule in entities[key].interactions:
                r = rule.model_dump()
                if ready(r):
                    items.update(rule.acquire_item_ids)
                    # The runtime also acquires items through explicit inventory
                    # operations; these are valid producers for transition gates.
                    if (
                        rule.inventory_operation in {"pickup", "initial", "recover"}
                        and rule.item_id
                    ):
                        items.add(rule.item_id)
                    facts.update(rule.reveal_entity_ids)
                    possible_flags.update(rule.set_flags.items())
                    if rule.outcome:
                        potential_outcomes.add(rule.outcome)
        for t in edges:
            if t["source_scene_node_id"] in potential_scenes and ready(t):
                potential_scenes.add(t["target_scene_node_id"])
        if before == (len(potential_scenes), len(items), len(facts), len(possible_flags)):
            break
    if not potential_scenes == scene_nodes:
        raise ValueError("conditional scene prerequisite cycle")
    flags = {k for e in entities.values() for r in e.interactions for k in r.set_flags}
    for t in edges:
        if not set(t.get("required_flags", {})) <= flags:
            raise ValueError("package_invalid: set(t.get('required_flags', {})) <= flags")
        if not set(t.get("required_item_ids", [])) <= entities.keys():
            raise ValueError(
                "package_invalid: set(t.get('required_item_ids', [])) <= entities.keys()"
            )
    outcomes = {r.outcome for e in entities.values() for r in e.interactions if r.outcome}
    if not outcomes == potential_outcomes:
        raise ValueError("ending prerequisite cycle")
    missing = {
        key: e.combat_template.missing()
        for key, e in entities.items()
        if e.combat_template and e.combat_template.missing()
    }
    operations = {
        key: {
            operation: entities[key].combat_template.missing(operation)
            if entities[key].combat_template
            else ["combat_template"]
            for operation in required
        }
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
        "entity_types": dict(Counter((e.type for e in entities.values()))),
        "transitions": len(edges),
        "outcomes": sorted(outcomes),
        "coverage_entries": len(package["coverage"]),
        "coverage_status": dict(Counter((e["status"] for e in package["coverage"]))),
        "missing_combat_values": missing,
        "required_npc_operations": operations,
        "operation_gaps": operation_gaps,
        "reviewer": package["reviewer"],
        "human_review": False,
        "source_accounting_complete": True,
        "static_validation_passed": True,
        "required_operations_ready": not operation_gaps,
        "runtime_acceptance": {"status": "not_run_by_import", "note": "静态校验不代表自然游戏通过"},
        "source_classification": dict(Counter(e["status"] for e in package["coverage"])),
        "source_gaps": [
            {k: e[k] for k in ("id", "status", "gaps")} for e in package["coverage"] if e["gaps"]
        ],
        "potentially_reachable_outcomes": sorted(potential_outcomes),
        "reachability_basis": "source bindings/prerequisites; dice/KP rulings still required",
    }
