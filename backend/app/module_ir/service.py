import asyncio
from uuid import uuid4

from sqlalchemy import delete

from app.domain.character import utc_now
from app.knowledge.text import normalize
from app.module_ir.parser import parse_module, stable_id, validate_source
from app.module_ir.repository import StructureRepository
from app.module_ir.schemas import EntityNodeBinding, StructureSnapshot
from app.persistence.module_ir_models import (
    ApprovedStructure,
    EntityNodeBindingRecord,
    StructureOverride,
)
from app.persistence.preparation_models import ModuleEntity, ModuleEntityRelation
from app.rooms.service import require


class ModuleStructureService:
    def __init__(self, agents):
        self.agents = agents
        self.repository = StructureRepository(agents.knowledge.repository)

    async def draft(self, session, preparation_id):
        row = await session.get(StructureOverride, str(preparation_id))
        require(row, "请先构建文档结构", 404)
        return row, StructureSnapshot.model_validate(row.document)

    async def build(self, preparation_id):
        async with self.agents.knowledge.index_lock:
            async with self.agents.rooms.transaction() as session:
                prep = await self.agents.preparation.get(session, preparation_id)
                require(prep.status != "stale", "stale structure：请重新索引并创建准备任务", 422)
                source = self.agents.knowledge.repository.source(prep.source_id, prep.source_hash)
                require(source, "来源版本不存在", 404)
                try:
                    await asyncio.to_thread(validate_source, source, self.agents.settings.data_dir)
                    ir = self.repository.get(
                        stable_id("ir1_", source.source_id, source.source_hash)
                    )
                    if ir is None:
                        ir = await asyncio.to_thread(
                            parse_module, source, self.agents.settings.data_dir
                        )
                except (ValueError, OSError) as error:
                    raise ValueError("module_structure_extraction_failed") from error
                ir = self.repository.store(ir)
                old = await session.get(StructureOverride, prep.id)
                if old:
                    require(
                        old.document["structure_version"] == ir.structure_version,
                        "现有校正引用不同版本，请创建新准备任务",
                    )
                else:
                    draft = StructureSnapshot(
                        snapshot_id=str(uuid4()),
                        preparation_id=prep.id,
                        preparation_version=prep.version,
                        source_id=prep.source_id,
                        source_hash=prep.source_hash,
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
                return await self.view(session, prep.id)

    async def view(self, session, preparation_id):
        row, draft = await self.draft(session, preparation_id)
        ir = self.repository.get(draft.structure_version)
        require(ir, "module_structure_missing：请重建相同来源版本", 409)
        prep = await self.agents.preparation.get(session, preparation_id)
        return {
            **draft.model_dump(mode="json"),
            "approved_snapshot_id": row.approved_snapshot_id,
            "stale": prep.status == "stale",
            "warnings": ir.warnings,
            "extraction_method": ir.extraction_method,
            "block_count": ir.block_count,
            "node_count": ir.node_count,
            "nodes": [
                {
                    **n.model_dump(mode="json"),
                    "original_depth": next(o.depth for o in ir.nodes if o.node_id == n.node_id),
                    "block_count": sum(b.node_id == n.node_id for b in ir.blocks),
                    "entity_count": sum(b.node_id == n.node_id for b in draft.entity_bindings),
                }
                for n in draft.nodes
            ],
        }

    async def preview(self, session, preparation_id, node_id):
        _, draft = await self.draft(session, preparation_id)
        node = next((n for n in draft.nodes if n.node_id == node_id), None)
        require(node, "node_not_in_structure", 404)
        ir = self.repository.get(draft.structure_version)
        require(ir, "module_structure_missing")
        selected, remaining = [], 1200
        for block in ir.blocks:
            if block.node_id != node_id or remaining <= 0:
                continue
            text = block.text[: min(420, remaining)]
            remaining -= len(text)
            selected.append(
                {
                    "block_id": block.block_id,
                    "block_type": block.block_type,
                    "text": text,
                    "source_position": block.source_position.model_dump(),
                    "truncated": len(text) < len(block.text),
                }
            )
        return {"node": node.model_dump(mode="json"), "blocks": selected, "preview_limit": 1200}

    def save_draft(self, row, draft):
        draft.approved = False
        draft = StructureSnapshot.model_validate(draft.model_dump())
        row.document = draft.model_dump(mode="json")
        row.approved_snapshot_id = None

    async def patch_node(self, session, preparation_id, node_id, patch):
        patch = type(patch).model_validate(
            self.agents.preparation.safe(patch.model_dump(exclude_unset=True))
        )
        row, draft = await self.draft(session, preparation_id)
        nodes = {n.node_id: n for n in draft.nodes}
        node = nodes.get(node_id)
        require(node, "node_not_in_structure", 404)
        values = patch.model_dump(exclude_unset=True, exclude={"initial_scene"})
        if "parent_node_id" in values:
            parent = nodes.get(values["parent_node_id"])
            require(node_id != draft.root_node_id and parent, "根节点不可移动且父节点必须存在", 422)
            require(
                parent.source_position.file_reference in {"", node.source_position.file_reference},
                "不能跨来源文件调整父节点",
                422,
            )
            cursor = parent
            while cursor:
                require(cursor.node_id != node_id, "父子关系不能成环", 422)
                cursor = nodes.get(cursor.parent_node_id)
            nodes[node.parent_node_id].child_ids.remove(node_id)
            parent.child_ids.append(node_id)
            parent.child_ids.sort(key=lambda value: nodes[value].source_order)
        for key, value in values.items():
            setattr(node, key, value)
        node.updated_at = utc_now()
        node.normalized_title = normalize(node.title)
        if patch.initial_scene:
            node.approved_type = "scene"
            draft.initial_scene_node_id = node_id

        def paths(current, path):
            current.depth = len(path)
            current.heading_path = [*path, current.title]
            for child in current.child_ids:
                paths(nodes[child], current.heading_path)

        paths(nodes[draft.root_node_id], [])
        ir = self.repository.get(draft.structure_version)
        require(ir, "module_structure_missing：请重建匹配版本")
        require(
            set(node.important_block_ids)
            <= {b.block_id for b in ir.blocks if b.node_id == node_id},
            "重要 block 不属于节点",
            422,
        )
        self.save_draft(row, draft)
        return node.model_dump(mode="json")

    async def entity_binding(self, session, preparation_id, body):
        row, draft = await self.draft(session, preparation_id)
        entity = await session.get(ModuleEntity, body.entity_id)
        require(
            entity
            and entity.preparation_id == draft.preparation_id
            and entity.status == "approved",
            "只能绑定本任务已批准实体",
            422,
        )
        require(body.source_hash == draft.source_hash, "entity_source_hash_mismatch", 422)
        require(
            all(
                ref["source_hash"] == draft.source_hash
                for ref in entity.document.get("source_references", [])
            ),
            "entity_source_hash_mismatch",
            422,
        )
        node = next((n for n in draft.nodes if n.node_id == body.node_id), None)
        require(
            node and node.included and node.approved_type == "scene",
            "实体必须绑定 included scene",
            422,
        )
        if entity.type == "scene":
            for existing_binding in draft.entity_bindings:
                if (
                    existing_binding.node_id == node.node_id
                    and existing_binding.entity_id != entity.id
                ):
                    existing_entity = await session.get(ModuleEntity, existing_binding.entity_id)
                    require(existing_entity.type != "scene", "scene 节点已有对应场景实体", 422)
            require(
                not any(
                    b.entity_id == entity.id and b.node_id != node.node_id
                    for b in draft.entity_bindings
                ),
                "scene 实体只能绑定一个节点",
                422,
            )
        if body.npc_entity_id:
            npc = await session.get(ModuleEntity, body.npc_entity_id)
            require(
                entity.type == "item"
                and npc
                and npc.type == "npc"
                and npc.status == "approved"
                and npc.preparation_id == draft.preparation_id,
                "物品持有人必须是本任务批准 NPC",
                422,
            )
        existing = next(
            (
                b
                for b in draft.entity_bindings
                if b.entity_id == entity.id and b.node_id == node.node_id
            ),
            None,
        )
        if existing:
            return existing.model_dump()
        binding = EntityNodeBinding(
            binding_id=str(uuid4()),
            **body.model_dump(),
            evidence_ids=entity.document.get("evidence_ids", []),
        )
        draft.entity_bindings.append(binding)
        session.add(
            EntityNodeBindingRecord(
                id=binding.binding_id,
                preparation_id=draft.preparation_id,
                document=binding.model_dump(),
            )
        )
        self.save_draft(row, draft)
        return binding.model_dump()

    async def remove_binding(self, session, preparation_id, binding_id):
        row, draft = await self.draft(session, preparation_id)
        require(
            any(b.binding_id == binding_id for b in draft.entity_bindings), "binding_not_found", 404
        )
        draft.entity_bindings = [b for b in draft.entity_bindings if b.binding_id != binding_id]
        await session.execute(
            delete(EntityNodeBindingRecord).where(
                EntityNodeBindingRecord.id == binding_id,
                EntityNodeBindingRecord.preparation_id == draft.preparation_id,
            )
        )
        self.save_draft(row, draft)
        return {"deleted": True}

    async def suggestions(self, session, preparation_id):
        _, draft = await self.draft(session, preparation_id)
        ir = self.repository.get(draft.structure_version)
        require(ir, "module_structure_missing")
        result = []
        for entity in await self.agents.preparation.entities(session, draft.preparation_id):
            if entity.status != "approved":
                continue
            pages = set(entity.document.get("source_pages", []))
            for node in draft.nodes:
                if node.approved_type != "scene" or not node.included:
                    continue
                candidates = [b for b in ir.blocks if b.node_id == node.node_id]
                reasons = []
                if any(b.page_reference in pages for b in candidates):
                    reasons.append("evidence_source_page")
                if any(entity.document["title"] in b.text for b in candidates):
                    reasons.append("exact_entity_title")
                if reasons:
                    result.append(
                        {
                            "entity_id": entity.id,
                            "node_id": node.node_id,
                            "source_hash": draft.source_hash,
                            "reasons": reasons,
                            "approved": False,
                        }
                    )
        return {"bindings": [b.model_dump() for b in draft.entity_bindings], "suggestions": result}

    async def transition(self, session, preparation_id, body, transition_id=None, remove=False):
        row, draft = await self.draft(session, preparation_id)
        if transition_id:
            require(
                any(t.transition_id == transition_id for t in draft.transitions),
                "transition_not_found",
                404,
            )
            draft.transitions = [t for t in draft.transitions if t.transition_id != transition_id]
        if not remove:
            body = type(body).model_validate(self.agents.preparation.safe(body.model_dump()))
            body.transition_id = transition_id or str(uuid4())
            approved_entities = {
                e.id
                for e in await self.agents.preparation.entities(session, draft.preparation_id)
                if e.status == "approved"
            }
            require(
                set(body.required_revealed_entity_ids) <= approved_entities,
                "转换条件必须引用批准实体",
                422,
            )
            # Keep a link to the existing relation where scene entities exist at both ends.
            ends = []
            entity_types = {
                e.id: e.type
                for e in await self.agents.preparation.entities(session, draft.preparation_id)
            }
            for node_id in (body.source_scene_node_id, body.target_scene_node_id):
                ends.append(
                    next(
                        (
                            b.entity_id
                            for b in draft.entity_bindings
                            if b.node_id == node_id and entity_types.get(b.entity_id) == "scene"
                        ),
                        None,
                    )
                )
            if all(ends):
                relation = ModuleEntityRelation(
                    id=str(uuid4()),
                    preparation_id=draft.preparation_id,
                    source_entity_id=ends[0],
                    target_entity_id=ends[1],
                    relation_type="leads_to",
                    status="approved" if body.approved else "draft",
                    document={
                        "keeper_note": body.condition_summary,
                        "evidence_ids": body.source_evidence,
                        "validation_errors": [],
                        "generated_by": "host",
                        "host_edited": True,
                    },
                )
                session.add(relation)
                body.relation_id = relation.id
            draft.transitions.append(body)
        self.save_draft(row, draft)
        return body.model_dump() if not remove else {"deleted": True}

    async def approve(self, session, preparation_id, body):
        row, draft = await self.draft(session, preparation_id)
        prep = await self.agents.preparation.get(session, preparation_id)
        require(prep.status == "approved", "请先批准第五批准备版本", 422)
        require(self.repository.get(draft.structure_version), "module_structure_missing")
        entities = {e.id: e for e in await self.agents.preparation.entities(session, prep.id)}
        for transition in draft.transitions:
            require(
                all(
                    e in entities and entities[e].status == "approved"
                    for e in transition.required_revealed_entity_ids
                ),
                "转换条件引用的实体未批准",
                422,
            )
        for binding in draft.entity_bindings:
            require(
                binding.entity_id in entities and entities[binding.entity_id].status == "approved",
                "绑定实体已失去批准",
                422,
            )
        for node in draft.nodes:
            if node.included and not node.approved_type:
                node.approved_type = node.detected_type
            if node.included and node.parent_node_id:
                require(
                    next(n.included for n in draft.nodes if n.node_id == node.parent_node_id),
                    "included 节点的父节点也必须 included",
                    422,
                )
        require(
            any(
                b.entity_id == prep.document["initial_scene_entity_id"]
                and b.node_id == draft.initial_scene_node_id
                for b in draft.entity_bindings
            ),
            "初始 scene 必须绑定准备任务的初始场景实体",
            422,
        )
        draft.approved, draft.incomplete = True, body.incomplete
        draft.preparation_version = prep.version
        draft.snapshot_id = str(uuid4())
        draft = StructureSnapshot.model_validate(draft.model_dump())
        session.add(
            ApprovedStructure(
                id=draft.snapshot_id, preparation_id=prep.id, document=draft.model_dump(mode="json")
            )
        )
        row.document = draft.model_dump(mode="json")
        row.approved_snapshot_id = draft.snapshot_id
        return draft.model_dump(mode="json")
