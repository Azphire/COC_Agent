"""Room-scoped approved snapshots; public views are explicit allowlists."""

import json
from uuid import uuid4

from sqlalchemy import delete, select

from app.agents.modules import Module, content_hash
from app.domain.character import utc_now
from app.persistence.agent_models import AgentCycle, AgentMemory, CheckRecord, ModuleSnapshot
from app.persistence.knowledge_models import RoomKnowledgeBinding
from app.persistence.preparation_models import (
    HostReviewRequest,
    PreparationSaveState,
    RoomEntityState,
    RoomPreparationBinding,
)
from app.preparation.schemas import ProposalArgs
from app.preparation.service import entity_view, row_view
from app.rooms.service import iso_utc, require


class RoomEntityService:
    def __init__(self, agents):
        self.agents, self.rooms = agents, agents.rooms

    async def binding(self, session, room_id):
        return await session.get(RoomPreparationBinding, room_id)

    async def rows(self, session, room_id):
        return list(
            await session.scalars(
                select(RoomEntityState)
                .where(RoomEntityState.room_id == room_id)
                .order_by(RoomEntityState.source_entity_id)
            )
        )

    @staticmethod
    def public_view(row):
        return {
            "id": row.source_entity_id,
            "type": row.entity_type,
            "title": row.snapshot["title"],
            "public_summary": row.frozen_public_summary,
            "source_references": row.frozen_source_references,
            "state": row.state,
            "revealed_event_seq": row.revealed_event_seq,
            "revealed_by": row.revealed_by,
            "revealed_time": iso_utc(row.revealed_time) if row.revealed_time else None,
            "correction_reference": row.correction_reference,
        }

    async def public(self, session, room_id):
        return [
            self.public_view(e)
            for e in await self.rows(session, room_id)
            if e.state in {"revealed", "corrected"}
        ]

    async def host(self, session, room_id):
        return [{**e.snapshot, **self.public_view(e)} for e in await self.rows(session, room_id)]

    async def bind(self, session, room, preparation_id):
        require(room.status == "lobby", "只允许未开始的房间显式绑定准备版本")
        require(not await self.agents.cycle(session, room.id, active=True), "房间有活动回合")
        prep = await self.agents.preparation.get(session, preparation_id)
        require(prep.status == "approved", "只能绑定当前 hash 的 approved preparation", 422)
        require(
            self.agents.knowledge.repository.available(prep.source_id, prep.source_hash),
            "knowledge_missing：来源版本不可用",
        )
        rows = await self.rows(session, room.id)
        require(
            not rows
            or all(e.revealed_by == room.host_member_id for e in rows if e.state != "hidden"),
            "已有游戏公开记录，不能重新绑定",
        )
        await session.execute(delete(RoomEntityState).where(RoomEntityState.room_id == room.id))
        entities = [
            e
            for e in await self.agents.preparation.entities(session, prep.id)
            if e.status == "approved"
        ]
        relations = [
            entity_view(r)
            for r in await self.agents.preparation.relations(session, prep.id)
            if r.status == "approved"
        ]
        binding = await self.binding(session, room.id)
        values = dict(
            preparation_id=prep.id,
            source_id=prep.source_id,
            source_hash=prep.source_hash,
            preparation_version=prep.version,
            initial_scene=prep.document["initial_scene_entity_id"],
            current_scene=prep.document["initial_scene_entity_id"],
            relations=relations,
            bound_at=utc_now(),
        )
        if binding:
            for key, value in values.items():
                setattr(binding, key, value)
        else:
            binding = RoomPreparationBinding(room_id=room.id, **values)
            session.add(binding)
        for entity in entities:
            doc = entity_view(entity)
            session.add(
                RoomEntityState(
                    room_id=room.id,
                    source_entity_id=entity.id,
                    entity_type=entity.type,
                    snapshot=doc,
                    state="hidden",
                    frozen_public_summary=doc["public_summary"],
                    frozen_source_references=doc["source_references"],
                )
            )
        knowledge = await session.get(RoomKnowledgeBinding, room.id)
        document = {
            "enabled": True,
            "rules": knowledge.document["rules"] if knowledge else [],
            "module": {"source_id": prep.source_id, "source_hash": prep.source_hash},
        }
        if knowledge:
            knowledge.document = document
        else:
            session.add(RoomKnowledgeBinding(room_id=room.id, document=document))
        scene = next(e for e in entities if e.id == binding.initial_scene)
        # Compatibility shell contains only the current public scene. All entity state is above.
        source = self.agents.knowledge.repository.source(prep.source_id, prep.source_hash)
        module = Module(
            id=source.module_id,
            title=prep.document["display_title"],
            version=str(prep.version),
            public_introduction=scene.document["public_summary"],
            keeper_brief="使用当前房间已批准实体；未批准事实必须请求主机审阅。",
            initial_scene=scene.id,
            scenes=[
                dict(
                    id=scene.id,
                    title=scene.document["title"],
                    public_description=scene.document["public_summary"],
                )
            ],
            npcs=[],
            clues=[],
            suggested_checks=[],
            completion_conditions=None,
        ).model_dump(mode="json")
        shell = await self.agents.module(session, room.id)
        state = {"scene_id": scene.id, "revealed_clues": [], "completed": False}
        if shell:
            shell.document, shell.content_hash, shell.state = module, content_hash(module), state
        else:
            session.add(
                ModuleSnapshot(
                    id=str(uuid4()),
                    room_id=room.id,
                    document=module,
                    content_hash=content_hash(module),
                    state=state,
                    enabled=True,
                )
            )
        await session.flush()
        self.rooms.append(
            session,
            room,
            "preparation.bound",
            room.host_member_id,
            {
                "preparation_id": prep.id,
                "version": prep.version,
                "source_hash": prep.source_hash,
                "title": prep.document["display_title"],
            },
        )
        self.rooms.append(
            session,
            room,
            "preparation.approved_entities",
            room.host_member_id,
            {"entity_ids": [e.id for e in entities]},
            "host_only",
        )
        await self.reveal(session, room, scene.id, room.host_member_id, host_override=True)
        await self.set_scene(session, room, binding, scene.id, room.host_member_id)
        for entity in entities:
            if entity.id != scene.id and entity.document["initial_visibility"] == "revealed":
                await self.reveal(session, room, entity.id, room.host_member_id)
        return row_view(binding)

    async def entity(self, session, room_id, entity_id):
        entity = await session.get(RoomEntityState, (room_id, entity_id))
        require(
            entity and entity.snapshot["status"] == "approved",
            "needs_host_ruling：实体不是本房间批准实体",
            422,
        )
        return entity

    async def check_conditions(self, session, room, entity, cycle_id=None, for_check=False):
        binding = await self.binding(session, room.id)
        pre = entity.snapshot["reveal_conditions"]
        require(not pre["scene_id"] or pre["scene_id"] == binding.current_scene, "实体不在当前场景")
        visible = {e["id"] for e in await self.public(session, room.id)}
        require(set(pre["required_entity_ids"]) <= visible, "实体前置条件尚未满足")
        if pre["successful_check"] and not for_check:
            checks = await session.scalars(
                select(CheckRecord).where(
                    CheckRecord.room_id == room.id,
                    CheckRecord.cycle_id == cycle_id,
                    CheckRecord.status == "resolved",
                )
            )
            require(
                any(
                    c.document.get("clue_id") == entity.source_entity_id
                    and c.document["result"]["passed"]
                    for c in checks
                ),
                "必须通过关联的真实检定",
            )

    async def reveal(self, session, room, entity_id, actor, cycle_id=None, host_override=False):
        entity = await self.entity(session, room.id, entity_id)
        if entity.state != "hidden":
            return {
                "entity_id": entity_id,
                "already_revealed": True,
                "event_seq": entity.revealed_event_seq,
            }
        require(entity.frozen_public_summary.strip(), "公开摘要为空，不能揭示", 422)
        if not host_override:
            await self.check_conditions(session, room, entity, cycle_id)
        entity.state, entity.revealed_by, entity.revealed_time = "revealed", actor, utc_now()
        event = self.rooms.append(
            session,
            room,
            "entity.revealed",
            actor,
            {**self.public_view(entity), "cycle_id": cycle_id},
        )
        entity.revealed_event_seq = event.seq
        event.payload = {**event.payload, "revealed_event_seq": event.seq}
        await self.observe(session, room, entity, event)
        if cycle_id:
            cycle = await session.get(AgentCycle, cycle_id)
            cycle.state = {
                **cycle.state,
                "revealed_entity_ids": list(
                    dict.fromkeys([*cycle.state.get("revealed_entity_ids", []), entity_id])
                ),
                "approved_entity_ids_used": list(
                    dict.fromkeys([*cycle.state.get("approved_entity_ids_used", []), entity_id])
                ),
            }
        return {"entity_id": entity_id, "event_seq": event.seq}

    async def observe(self, session, room, entity, event, correction=False):
        previous = None
        if correction:
            for memory in await session.scalars(
                select(AgentMemory).where(
                    AgentMemory.room_id == room.id,
                    AgentMemory.scope == "public",
                    AgentMemory.active.is_(True),
                )
            ):
                if entity.source_entity_id in memory.content:
                    memory.active = False
                    previous = memory.id
        session.add(
            AgentMemory(
                id=str(uuid4()),
                room_id=room.id,
                profile_id=None,
                kind="observation",
                scope="public",
                content=json.dumps(self.public_view(entity), ensure_ascii=False),
                source_event_ids=[event.seq],
                salience=10,
                active=True,
                supersedes_id=previous,
            )
        )

    async def correct(self, session, room, entity_id, body):
        entity = await self.entity(session, room.id, entity_id)
        require(entity.state != "hidden", "只能修正已公开实体")
        if entity.frozen_public_summary == body.public_summary:
            return self.public_view(entity)
        entity.frozen_public_summary, entity.state = body.public_summary, "corrected"
        event = self.rooms.append(
            session,
            room,
            "entity.corrected",
            room.host_member_id,
            {**self.public_view(entity), "reason": body.reason},
        )
        entity.correction_reference = event.seq
        event.payload = {**event.payload, "correction_reference": event.seq}
        await self.observe(session, room, entity, event, correction=True)
        binding = await self.binding(session, room.id)
        if binding.current_scene == entity_id:
            await self.set_scene(session, room, binding, entity_id, room.host_member_id)
        return self.public_view(entity)

    async def set_scene(self, session, room, binding, entity_id, actor, cycle_id=None):
        entity = await self.entity(session, room.id, entity_id)
        require(entity.entity_type == "scene", "目标不是批准场景", 422)
        binding.current_scene = entity_id
        room.session_state = {
            **room.session_state,
            "scene_title": entity.snapshot["title"],
            "scene_summary": entity.frozen_public_summary,
        }
        shell = await self.agents.module(session, room.id)
        shell.document = {
            **shell.document,
            "initial_scene": entity_id,
            "scenes": [
                {
                    "id": entity_id,
                    "title": entity.snapshot["title"],
                    "public_description": entity.frozen_public_summary,
                    "keeper_notes": "",
                }
            ],
        }
        shell.state = {**shell.state, "scene_id": entity_id}
        self.rooms.append(
            session,
            room,
            "scene.updated",
            actor,
            {
                "scene_id": entity_id,
                "scene_title": entity.snapshot["title"],
                "scene_summary": entity.frozen_public_summary,
                "cycle_id": cycle_id,
            },
        )

    async def transition(self, session, room, run, entity_id, host_override=False):
        entity = await self.entity(session, room.id, entity_id)
        require(entity.entity_type == "scene", "目标必须是批准场景", 422)
        binding = await self.binding(session, room.id)
        if binding.current_scene == entity_id:
            return {"scene_id": entity_id, "unchanged": True}
        linked = any(
            r["relation_type"] == "leads_to"
            and r["source_entity_id"] == binding.current_scene
            and r["target_entity_id"] == entity_id
            for r in binding.relations
        )
        if not linked and not host_override:
            return await self.propose(
                session,
                room,
                run,
                ProposalArgs(
                    request_type="scene_transition",
                    entity_type="scene",
                    entity_id=entity_id,
                    proposed_title=entity.snapshot["title"],
                    proposed_public_summary=entity.frozen_public_summary,
                    keeper_reason="当前场景没有批准的 leads_to 关系",
                    related_entity_ids=[entity_id],
                ),
            )
        await self.reveal(
            session, room, entity_id, run.actor_member_id, run.cycle_id, host_override
        )
        await self.set_scene(session, room, binding, entity_id, run.actor_member_id, run.cycle_id)
        cycle = await session.get(AgentCycle, run.cycle_id)
        cycle.state = {**cycle.state, "scene_transition": entity_id}
        return {"scene_id": entity_id}

    async def propose(self, session, room, run, args):
        binding = await self.binding(session, room.id)
        require(binding, "needs_host_ruling：请先绑定模组准备版本", 422)
        cycle = await session.get(AgentCycle, run.cycle_id)
        previous = await session.scalar(
            select(HostReviewRequest).where(HostReviewRequest.cycle_id == cycle.id)
        )
        if previous:
            return {
                "needs_host_ruling": True,
                "review_id": previous.id,
                "review_limit_reached": True,
            }
        pending_id = cycle.state.get("pending_check_id")
        pending = await session.get(CheckRecord, pending_id) if pending_id else None
        require(not pending or pending.status != "pending", "已有检定等待项，不能创建并行审阅")
        approved = {e.source_entity_id for e in await self.rows(session, room.id)}
        require(set(args.related_entity_ids) <= approved, "相关实体不属于当前房间", 422)
        if args.entity_id:
            await self.entity(session, room.id, args.entity_id)
        evidence = await self.agents.knowledge.evidence_for_run(
            session, run.id, room.id, run.profile_id
        )
        selected = [evidence[i] for i in args.evidence_ids if i in evidence]
        valid = (
            bool(args.evidence_ids)
            and len(selected) == len(set(args.evidence_ids))
            and all(
                (e["source_id"], e["source_hash"], e["source_kind"])
                == (binding.source_id, binding.source_hash, "module")
                for e in selected
            )
        )
        if not args.entity_id and not valid:
            return {"needs_host_ruling": True, "code": "current_run_evidence_required"}
        if args.evidence_ids:
            require(valid, "needs_host_ruling：必须引用当前 run 的同版本模组证据", 422)
        review = HostReviewRequest(
            id=str(uuid4()),
            room_id=room.id,
            cycle_id=cycle.id,
            agent_run_id=run.id,
            request_type=args.request_type,
            status="pending",
            document={
                **args.model_dump(),
                "evidence": selected,
                "host_response": "",
                "applied": False,
            },
        )
        session.add(review)
        cycle.state = {
            **cycle.state,
            "pending_review_id": review.id,
            "review_count": 1,
            "proposed_entity_ids": [args.entity_id] if args.entity_id else [],
        }
        self.rooms.append(
            session,
            room,
            "review.requested",
            run.actor_member_id,
            {"review_id": review.id, "cycle_id": cycle.id, **review.document},
            "host_only",
        )
        self.rooms.append(
            session,
            room,
            "review.waiting",
            run.actor_member_id,
            {"cycle_id": cycle.id, "text": "[主机审阅] 等待主机确认调查内容。"},
        )
        return {"review_id": review.id, "pending": True}

    async def resolve(self, session, room, review_id, decision, body):
        review = await session.get(HostReviewRequest, review_id)
        require(review and review.room_id == room.id, "审阅请求不存在", 404)
        if review.status != "pending":
            return entity_view(review)
        cycle = await session.get(AgentCycle, review.cycle_id)
        require(
            cycle.status in {"waiting_for_review", "failed"}
            and cycle.state.get("wait_reason") == "host_review",
            "回合未等待此审阅",
        )
        if decision != "rejected":
            summary = (
                body.public_summary
                if body.public_summary is not None
                else review.document["proposed_public_summary"]
            )
            require(summary.strip(), "批准前必须填写可公开摘要", 422)
            if review.document.get("entity_id") and body.entity_type:
                entity = await self.entity(session, room.id, review.document["entity_id"])
                require(
                    entity.state == "hidden" or entity.entity_type == body.entity_type,
                    "已公开实体类型需通过另一个修正实体说明",
                    422,
                )
            if body.public_summary is not None or body.entity_type is not None:
                decision = "edited"
            if review.request_type == "scene_transition":
                require(
                    (body.entity_type or review.document["entity_type"]) == "scene",
                    "场景转换必须批准 scene",
                    422,
                )
        review.status, review.resolved_at = decision, utc_now()
        review.document = {
            **review.document,
            "host_response": body.host_response,
            "response": body.model_dump(),
            "host_edited": decision == "edited",
        }
        self.rooms.append(
            session,
            room,
            "review.resolved",
            room.host_member_id,
            {"review_id": review.id, "decision": decision, **review.document},
            "host_only",
        )
        self.rooms.append(
            session,
            room,
            "review.status",
            room.host_member_id,
            {
                "cycle_id": cycle.id,
                "status": decision,
                "text": "[主机审阅] "
                + {"approved": "已批准", "edited": "已编辑并批准", "rejected": "已拒绝"}[decision],
            },
        )
        return entity_view(review)

    async def apply_review(self, session, room, review, run):
        if review.document.get("applied"):
            return review.document.get("result", {})
        require(review.status in {"approved", "edited", "rejected"}, "审阅尚未解决")
        if review.status == "rejected":
            result = {"status": "rejected", "needs_host_ruling": True}
        else:
            doc = review.document
            response = doc["response"]
            summary = (
                response["public_summary"]
                if response.get("public_summary") is not None
                else doc["proposed_public_summary"]
            )
            entity_id = doc.get("entity_id")
            if entity_id:
                entity = await self.entity(session, room.id, entity_id)
                if response.get("entity_type") and response["entity_type"] != entity.entity_type:
                    require(entity.state == "hidden", "已公开实体类型需通过另一个修正实体说明", 422)
                    entity.entity_type = response["entity_type"]
                    entity.snapshot = {
                        **entity.snapshot,
                        "type": entity.entity_type,
                        "host_edited": True,
                    }
                if summary != entity.frozen_public_summary:
                    if entity.state == "hidden":
                        entity.frozen_public_summary = summary
                        entity.snapshot = {
                            **entity.snapshot,
                            "public_summary": summary,
                            "host_edited": True,
                        }
                    else:
                        from app.preparation.schemas import Correction

                        await self.correct(
                            session,
                            room,
                            entity_id,
                            Correction(public_summary=summary, reason="主机审阅修正公开信息"),
                        )
            else:
                from app.preparation.schemas import EntityFields

                entity_id = str(uuid4())
                entity_type = response.get("entity_type") or doc["entity_type"]
                if review.request_type == "scene_transition":
                    require(entity_type == "scene", "场景转换必须批准 scene", 422)
                fields = EntityFields(
                    type=entity_type,
                    title=doc["proposed_title"],
                    public_summary=summary,
                    evidence_ids=doc["evidence_ids"],
                ).model_dump()
                refs = [
                    {
                        k: e[k]
                        for k in (
                            "source_id",
                            "source_hash",
                            "source_title",
                            "physical_page",
                            "page_kind",
                        )
                    }
                    for e in doc["evidence"]
                ]
                entity = RoomEntityState(
                    room_id=room.id,
                    source_entity_id=entity_id,
                    entity_type=entity_type,
                    snapshot={
                        **fields,
                        "id": entity_id,
                        "status": "approved",
                        "version": 1,
                        "generated_by": "model",
                        "host_edited": review.status == "edited",
                        "approved_review_id": review.id,
                        "source_references": refs,
                    },
                    state="hidden",
                    frozen_public_summary=summary,
                    frozen_source_references=refs,
                )
                session.add(entity)
                await session.flush()
            if review.request_type == "scene_transition":
                result = await self.transition(session, room, run, entity_id, host_override=True)
            else:
                result = await self.reveal(
                    session, room, entity_id, room.host_member_id, run.cycle_id, host_override=True
                )
            result = {**result, "status": review.status}
        review.document = {**review.document, "applied": True, "result": result}
        cycle = await session.get(AgentCycle, review.cycle_id)
        cycle.state = {**cycle.state, "review_result": result}
        return result

    async def save(self, session, room, snapshot):
        binding = await self.binding(session, room.id)
        if not binding:
            return
        reviews = list(
            await session.scalars(
                select(HostReviewRequest).where(HostReviewRequest.room_id == room.id)
            )
        )
        session.add(
            PreparationSaveState(
                snapshot_id=snapshot.id,
                document={
                    "binding": row_view(binding),
                    "entities": [row_view(e) for e in await self.rows(session, room.id)],
                    "reviews": [row_view(r) for r in reviews],
                },
            )
        )

    async def load(self, session, room, snapshot):
        saved = await session.get(PreparationSaveState, snapshot.id)
        binding = await self.binding(session, room.id)
        require(saved or not binding, "此旧存档没有实体快照，请使用绑定后的存档")
        if not saved:
            return
        from datetime import datetime

        for key, value in saved.document["binding"].items():
            setattr(binding, key, datetime.fromisoformat(value) if key == "bound_at" else value)
        # Reveals/corrections are monotonic: players already received newer public facts.
        for doc in saved.document["entities"]:
            row = await session.get(RoomEntityState, (room.id, doc["source_entity_id"]))
            if row is None:
                doc = dict(doc)
                if doc["revealed_time"]:
                    doc["revealed_time"] = datetime.fromisoformat(doc["revealed_time"])
                session.add(RoomEntityState(**doc))
        for doc in saved.document["reviews"]:
            review = await session.get(HostReviewRequest, doc["id"])
            if review and review.status == "pending":
                cycle = await session.get(AgentCycle, review.cycle_id)
                cycle.status = "waiting_for_review"
                cycle.state = {
                    **cycle.state,
                    "status": "waiting_for_review",
                    "wait_reason": "host_review",
                    "pending_review_id": review.id,
                    "safe_error": None,
                }
        current = {
            e.source_entity_id: e for e in await self.rows(session, room.id) if e.state != "hidden"
        }
        for memory in await session.scalars(
            select(AgentMemory).where(
                AgentMemory.room_id == room.id,
                AgentMemory.scope == "public",
                AgentMemory.kind == "observation",
            )
        ):
            try:
                doc = json.loads(memory.content)
            except ValueError:
                continue
            if isinstance(doc, dict) and doc.get("id") in current:
                row = current[doc["id"]]
                memory.active = doc.get("public_summary") == row.frozen_public_summary

    async def reconcile_scene(self, session, room):
        binding = await self.binding(session, room.id)
        if not binding:
            return
        entity = await self.entity(session, room.id, binding.current_scene)
        room.session_state = {
            **room.session_state,
            "scene_title": entity.snapshot["title"],
            "scene_summary": entity.frozen_public_summary,
        }
        shell = await self.agents.module(session, room.id)
        shell.document = {
            **shell.document,
            "initial_scene": entity.source_entity_id,
            "scenes": [
                {
                    "id": entity.source_entity_id,
                    "title": entity.snapshot["title"],
                    "public_description": entity.frozen_public_summary,
                    "keeper_notes": "",
                }
            ],
        }
        shell.state = {**shell.state, "scene_id": binding.current_scene}
