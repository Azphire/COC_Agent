import asyncio
import hashlib
import json
import re
import time
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.agents.modules import Module, content_hash, public_module
from app.knowledge.indexer import KnowledgeIndexer
from app.knowledge.repository import KnowledgeRepository
from app.knowledge.retriever import KnowledgeRetriever
from app.knowledge.schemas import GroundedClaim
from app.knowledge.text import TERMS, normalize
from app.persistence.agent_models import AgentMemory, ModuleSnapshot
from app.persistence.knowledge_models import (
    ClaimRecord,
    KnowledgeSaveState,
    RetrievalRecord,
    RoomKnowledgeBinding,
)
from app.rooms.service import require

FLAVOR = ("调查仍在继续。", "四周安静下来。", "你们暂时停下，思考下一步行动。")


class KnowledgeService:
    def __init__(self, agents):
        self.agents = agents
        settings = agents.settings
        from pathlib import Path

        require(
            settings.knowledge_path.resolve()
            != Path(make_url(settings.database_url).database).resolve(),
            "知识数据库必须独立于游戏数据库",
            422,
        )
        self.repository = KnowledgeRepository(settings.knowledge_path)
        self.indexer = KnowledgeIndexer(settings.data_dir, self.repository)
        self.retriever = KnowledgeRetriever(self.repository)
        self.index_lock = asyncio.Lock()

    async def binding(self, session, room_id):
        row = await session.get(RoomKnowledgeBinding, room_id)
        return row.document if row else None

    async def view(self, session, room_id):
        binding = await self.binding(session, room_id)
        if not binding:
            return {"enabled": False, "rules": [], "module": None, "knowledge_missing": False}
        missing = []
        for ref in binding["rules"] + ([binding["module"]] if binding.get("module") else []):
            if not self.repository.available(**ref):
                missing.append(ref)
        return {
            **binding,
            "knowledge_missing": bool(binding["enabled"] and missing),
            "missing": missing,
        }

    async def require_available(self, session, room_id):
        require(
            not (await self.view(session, room_id))["knowledge_missing"],
            "knowledge_missing：请重新索引相同版本，或由主机明确重新绑定",
            409,
        )

    async def bind(self, session, room, body, member_id):
        prepared = await self.agents.entities.binding(session, room.id)
        if prepared:
            require(
                body.enabled
                and body.module
                and (body.module.source_id, body.module.source_hash)
                == (prepared.source_id, prepared.source_hash),
                "准备房间必须保留绑定的精确模组版本",
                422,
            )
        document = body.model_dump(exclude={"opening_scene"})
        module_source = None
        for ref in document["rules"]:
            source = self.repository.source(**ref)
            require(
                source
                and source.chunk_count
                and source.kind != "module"
                and source.edition == "coc7"
                and source.visibility == "public_rules",
                "规则来源不存在、未索引或版本不符",
                422,
            )
        if document["module"]:
            module_source = self.repository.source(**document["module"])
            require(
                module_source and module_source.kind == "module" and module_source.chunk_count,
                "模组没有可用文本索引",
                422,
            )
            snapshot = await self.agents.module(session, room.id)
            if snapshot:
                require(
                    snapshot.document["id"] == module_source.module_id,
                    "模组知识源与房间模组不同，请新建房间",
                    422,
                )
            else:
                description = body.opening_scene or "等待主机公开开场场景。"
                module = Module(
                    id=module_source.module_id,
                    title=module_source.title,
                    version=module_source.version or "local",
                    public_introduction=description,
                    keeper_brief=(
                        "本地模组原文只能通过 search_module 检索。公开内容以场景事件为准。"
                    ),
                    initial_scene="opening",
                    scenes=[dict(id="opening", title="开场", public_description=description)],
                    npcs=[],
                    clues=[],
                    suggested_checks=[],
                    completion_conditions=None,
                )
                data = module.model_dump(mode="json")
                session.add(
                    ModuleSnapshot(
                        id=str(uuid4()),
                        room_id=room.id,
                        document=data,
                        content_hash=content_hash(data),
                        state={"scene_id": "opening", "revealed_clues": [], "completed": False},
                        enabled=True,
                    )
                )
                room.session_state = {
                    **room.session_state,
                    "scene_title": "开场",
                    "scene_summary": description,
                }
                self.agents.rooms.append(
                    session,
                    room,
                    "module.bound",
                    member_id,
                    {"module_id": module.id, "title": module.title},
                )
                self.agents.rooms.append(
                    session,
                    room,
                    "scene.updated",
                    member_id,
                    {"scene_id": "opening", "scene_title": "开场", "scene_summary": description},
                )
        current = await session.get(RoomKnowledgeBinding, room.id)
        if current:
            current.document = document
        else:
            session.add(RoomKnowledgeBinding(room_id=room.id, document=document))
        self.agents.rooms.append(session, room, "knowledge.bound", member_id, document)

    async def search(
        self,
        session,
        room,
        *,
        run_id,
        profile,
        actor_id,
        query,
        kind,
        top_k=4,
        scene_id=None,
        entity_id=None,
        public_only=False,
    ):
        binding = await self.binding(session, room.id)
        require(binding and binding["enabled"], "房间未启用知识来源", 422)
        keeper = profile.role == "keeper" and not public_only
        require(kind != "module" or keeper, "调查员不能检索隐藏模组", 403)
        refs = (
            binding["rules"]
            if kind == "rules"
            else ([binding["module"]] if binding.get("module") else [])
        )
        query = await self.agents.sanitize(session, room, query[:500])
        start = time.monotonic()
        evidence = self.retriever.search(
            query,
            refs=refs,
            run_id=run_id,
            kind=kind,
            keeper=keeper,
            top_k=top_k,
            scene_id=scene_id,
            entity_id=entity_id,
        )
        evidence = await self.agents.sanitize(session, room, evidence)
        record = RetrievalRecord(
            id=str(uuid4()),
            room_id=room.id,
            run_id=run_id,
            profile_id=profile.id,
            actor_member_id=actor_id,
            query=query,
            query_hash=hashlib.sha256(query.encode()).hexdigest(),
            source_filters={"kind": kind, "refs": refs, "edition": "coc7", "keeper": keeper},
            evidence=evidence,
            injected_ids=[],
            latency_ms=int((time.monotonic() - start) * 1000),
        )
        session.add(record)
        return evidence, record

    async def evidence_for_run(self, session, run_id, room_id, profile_id, public_only=False):
        rows = await session.scalars(
            select(RetrievalRecord).where(
                RetrievalRecord.run_id == run_id,
                RetrievalRecord.room_id == room_id,
                RetrievalRecord.profile_id == profile_id,
            )
        )
        return {
            e["evidence_id"]: e
            for row in rows
            for e in row.evidence
            if not public_only or e["visibility"] == "public_rules"
        }

    async def get_excerpt(self, session, room, run, evidence_id, profile):
        evidence = await self.evidence_for_run(
            session, run.id, room.id, profile.id, profile.role != "keeper"
        )
        require(evidence_id in evidence, "证据不属于当前 run 或不可见", 403)
        return evidence[evidence_id]

    async def pre_context(self, session, room, binding, profile, run_id, context, budget):
        configured = await self.binding(session, room.id)
        if not configured or not configured["enabled"]:
            return [], []
        await self.require_available(session, room.id)
        public = context["phase"] == "narrate_publicly" or profile.role != "keeper"
        trigger = context.get("triggering_action") or {}
        action = trigger.get("payload", {}).get("text", "")
        query = action[:400]
        kinds = []
        if (
            any(
                word in action
                for word in (
                    "规则",
                    "检定",
                    "骰",
                    "成功",
                    "失败",
                    "技能",
                    "属性",
                    "车卡",
                    "职业",
                    "年龄",
                    "教育",
                    "幸运",
                    "判定",
                    "侦查",
                )
            )
            or context["checks"]
        ):
            kinds.append("rules")
        if (
            not public
            and configured.get("module")
            and (not context.get("structure_navigation") or context.get("structure_incomplete"))
        ):
            kinds.append("module")
        collected, records = [], []
        for kind in kinds:
            search_query = query or "开场"
            if kind == "rules":
                concepts = [term for term in TERMS if term in action]
                if concepts:
                    search_query = " ".join(concepts)
            elif kind == "module":
                current_id = context["public_state"].get("scene_id")
                scene = next(
                    (s for s in context["module"].get("scenes", []) if s["id"] == current_id), {}
                )
                if context.get("prepared_module"):
                    scene = next(
                        (
                            e
                            for e in context["module"].get("approved_entities", [])
                            if e["id"] == current_id
                        ),
                        {},
                    )
                search_query = (
                    (scene.get("public_description") or scene.get("public_summary", ""))[:100]
                    or scene.get("title")
                    or "开场"
                )
                if context.get("prepared_module"):
                    # Approved scene titles are stable retrieval anchors; public
                    # prose may contain many atmospheric words absent in the source.
                    action_topic = re.split(r"[，,。.!！?？;；\n]", query, maxsplit=1)[0][:80]
                    search_query = " ".join(filter(None, (scene.get("title"), action_topic)))
            evidence, record = await self.search(
                session,
                room,
                run_id=run_id,
                profile=profile,
                actor_id=binding.member_id,
                query=search_query,
                kind=kind,
                public_only=public,
            )
            collected.extend(evidence)
            records.append(record)
            if kind == "module" and context.get("structure_navigation"):
                record.source_filters = {
                    **record.source_filters,
                    "scope": "global",
                    "reason": "structure_incomplete",
                }
                context["module_context_audit"] = {
                    **context["module_context_audit"],
                    "context_mode": "global_fallback",
                    "fallback_reason": "structure_incomplete",
                }
        # Apply permission first, then source diversity, score and bounded JSON size.
        selected = KnowledgeContextBuilder.select(collected, budget, public)
        selected_ids = {e["evidence_id"] for e in selected}
        injected = {e["evidence_id"]: e for e in selected}
        for record in records:
            # Validation must use exactly the bounded excerpts supplied to this
            # run, not discarded candidates or their longer pre-budget versions.
            record.source_filters = {
                **record.source_filters,
                "candidate_count": len(record.evidence),
            }
            record.evidence = [
                injected[e["evidence_id"]] for e in record.evidence if e["evidence_id"] in injected
            ]
            record.injected_ids = [
                e["evidence_id"] for e in record.evidence if e["evidence_id"] in selected_ids
            ]
        return selected, records

    async def validate_claim(self, session, room, run, claim, public_only=False):
        claim = GroundedClaim.model_validate(claim)
        if claim.node_ids:
            require(claim.category == "module_fact", "node 只能支持 module_fact", 422)
            return await self.agents.module_context.validate_node_claim(
                session, room, run, claim, public_only
            )
        evidence = await self.evidence_for_run(
            session, run.id, room.id, run.profile_id, public_only
        )
        selected = []
        for evidence_id in claim.evidence_ids:
            require(evidence_id in evidence, "needs_host_ruling：缺少当前 run 的有效证据", 422)
            selected.append(evidence[evidence_id])
        configured = await self.binding(session, room.id)
        refs = (configured or {}).get("rules", []) + (
            [(configured or {}).get("module")] if (configured or {}).get("module") else []
        )
        versions = {(r["source_id"], r["source_hash"]) for r in refs}
        require(
            all(
                (e["source_id"], e["source_hash"]) in versions and e["edition"] == "coc7"
                for e in selected
            ),
            "needs_host_ruling：来源版本与房间不符",
            422,
        )
        module = await self.agents.module(session, room.id)
        visible = public_module(module)
        all_entities = {
            e["id"]: e for name in ("scenes", "npcs", "clues") for e in module.document[name]
        }
        public_entities = {
            e["id"]: e for e in [visible["scene"], *visible["npcs"], *visible["clues"]]
        }
        if await self.agents.entities.binding(session, room.id):
            all_entities = {e["id"]: e for e in await self.agents.entities.host(session, room.id)}
            public_entities = {
                e["id"]: e for e in await self.agents.entities.public(session, room.id)
            }
        if claim.category == "rule":
            require(len(claim.statement) >= 12, "needs_host_ruling：章节标题不是规则说明", 422)
            require(
                selected
                and all(
                    e["source_kind"] in {"rulebook", "investigator_handbook"}
                    and e["visibility"] == "public_rules"
                    for e in selected
                ),
                "needs_host_ruling：规则陈述需要规则证据",
                422,
            )
            # Extractive claims prevent attaching a valid ID to invented rule numbers.
            require(
                any(normalize(claim.statement) in normalize(e["excerpt"]) for e in selected),
                "needs_host_ruling：规则陈述超出证据，请使用短引文",
                422,
            )
        elif claim.category == "module_fact":
            entities = public_entities if claim.visibility == "public" else all_entities
            require(
                all(e in entities for e in claim.entity_ids),
                "needs_host_ruling：实体不可见或不存在",
                422,
            )
            require(selected or claim.entity_ids, "needs_host_ruling：模组事实没有来源", 422)
            require(
                all(e["source_kind"] == "module" for e in selected), "模组事实不能引用规则来源", 422
            )
            require(claim.visibility != "public" or not selected, "隐藏模组证据不能直接发布", 403)
            supporting = [e["excerpt"] for e in selected]
            for entity_id in claim.entity_ids:
                entity = entities[entity_id]
                supporting += [
                    str(entity.get(key, ""))
                    for key in ("public_description", "public_summary", "content", "title", "name")
                ]
                if claim.visibility == "keeper_only":
                    supporting.append(str(entity.get("keeper_summary", "")))
            require(
                any(normalize(claim.statement) in normalize(text) for text in supporting),
                "needs_host_ruling：事实陈述超出来源",
                422,
            )
        else:
            require(
                claim.statement in FLAVOR and not claim.evidence_ids and not claim.entity_ids,
                "needs_host_ruling：自由描写仅限无事实含义的气氛短句",
                422,
            )
        if public_only:
            require(claim.visibility == "public", "私密陈述不能进入公开叙事", 403)
        return {
            **claim.model_dump(),
            "sources": [
                {
                    k: e[k]
                    for k in (
                        "evidence_id",
                        "source_id",
                        "source_hash",
                        "source_title",
                        "source_kind",
                        "physical_page",
                        "page_kind",
                        "page_label",
                        "section",
                        "excerpt",
                    )
                }
                for e in selected
            ],
        }

    async def record_claim(self, session, room, run, document, event_seq=None):
        claim_id = run.id + ":" + document["claim_id"]
        if await session.get(ClaimRecord, claim_id):
            return
        if document["entity_ids"]:
            from app.persistence.agent_models import AgentCycle

            cycle = await session.get(AgentCycle, run.cycle_id)
            cycle.state = {
                **cycle.state,
                "approved_entity_ids_used": list(
                    dict.fromkeys(
                        [*cycle.state.get("approved_entity_ids_used", []), *document["entity_ids"]]
                    )
                ),
            }
        memory_id = None
        if document["category"] != "flavor":
            memory_id = str(uuid4())
            # No arbitrary model-written canonical memory. Only validated extractive claims.
            session.add(
                AgentMemory(
                    id=memory_id,
                    room_id=room.id,
                    profile_id=run.profile_id,
                    kind="observation",
                    scope="public" if document["visibility"] == "public" else "keeper_only",
                    content=json.dumps(document, ensure_ascii=False),
                    source_event_ids=[event_seq] if event_seq else [],
                    salience=8,
                    active=True,
                )
            )
        session.add(
            ClaimRecord(
                id=claim_id,
                room_id=room.id,
                run_id=run.id,
                document=document,
                event_seq=event_seq,
                memory_id=memory_id,
            )
        )

    async def save(self, session, room, snapshot):
        binding = await self.binding(session, room.id)
        claims = list(
            await session.scalars(select(ClaimRecord).where(ClaimRecord.room_id == room.id))
        )
        session.add(
            KnowledgeSaveState(
                snapshot_id=snapshot.id,
                document={"binding": binding, "claims": [c.document for c in claims]},
            )
        )

    async def load(self, session, room, snapshot):
        saved = await session.get(KnowledgeSaveState, snapshot.id)
        current = await session.get(RoomKnowledgeBinding, room.id)
        document = saved.document.get("binding") if saved else None
        if current:
            if document:
                current.document = document
            else:
                await session.delete(current)
        elif document:
            session.add(RoomKnowledgeBinding(room_id=room.id, document=document))


class KnowledgeContextBuilder:
    @staticmethod
    def public_claim_options(context):
        """Small extractive candidates; the model must still select and validators recheck."""
        options = []
        action = (context.get("triggering_action") or {}).get("payload", {}).get("text", "")
        concepts = [term for term in TERMS if term in action]
        for evidence in context.get("RULE_EVIDENCE", [])[:2]:
            sentences = re.findall(r"[^。！？]{11,160}[。！？]", normalize(evidence["excerpt"]))
            if not sentences:
                continue
            sentences.sort(key=lambda s: -sum(term in s for term in concepts))
            options.append(
                dict(
                    claim_id=f"rule_{len(options)}",
                    category="rule",
                    statement=sentences[0].strip(),
                    evidence_ids=[evidence["evidence_id"]],
                    entity_ids=[],
                    visibility="public",
                )
            )
        scene = context["module"]["scene"]
        for entity in context.get("public_entities", []):
            if entity["type"] != "scene":
                options.append(
                    dict(
                        claim_id="entity_" + entity["id"],
                        category="module_fact",
                        statement=entity["public_summary"][:200],
                        evidence_ids=[],
                        entity_ids=[entity["id"]],
                        visibility="public",
                    )
                )
        options.append(
            dict(
                claim_id="scene",
                category="module_fact",
                statement=scene["public_description"][:200],
                evidence_ids=[],
                entity_ids=[scene["id"]],
                visibility="public",
            )
        )
        return options

    @staticmethod
    def select(evidence, budget, public_only):
        allowed = [e for e in evidence if not public_only or e["visibility"] == "public_rules"]
        allowed.sort(key=lambda e: (-e["score"], e["evidence_id"]))
        used_sources, used_kinds, first, diverse, rest = set(), set(), [], [], []
        for item in allowed:
            kind = "module" if item["source_kind"] == "module" else "rules"
            if kind not in used_kinds:
                first.append(item)
            elif item["source_id"] not in used_sources:
                diverse.append(item)
            else:
                rest.append(item)
            used_kinds.add(kind)
            used_sources.add(item["source_id"])
        result, ids = [], set()
        for item in first + diverse + rest:
            candidate = {**item, "excerpt": item["excerpt"][:280]}
            excess = len(json.dumps([*result, candidate], ensure_ascii=False)) - budget
            if excess > 0:
                candidate["excerpt"] = candidate["excerpt"][
                    : max(100, len(candidate["excerpt"]) - excess)
                ]
            if (
                item["evidence_id"] not in ids
                and len(json.dumps([*result, candidate], ensure_ascii=False)) <= budget
            ):
                result.append(candidate)
                ids.add(item["evidence_id"])
        return result
