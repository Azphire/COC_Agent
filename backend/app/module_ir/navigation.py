import json
from uuid import uuid4

from sqlalchemy import select

from app.agents.modules import content_hash
from app.domain.character import utc_now
from app.module_ir.schemas import RoomModuleNavigationState, StructureSnapshot, TransitionRequest
from app.persistence.agent_models import AgentCycle, AgentMemory, CheckRecord
from app.persistence.module_ir_models import (
    ApprovedStructure,
    NavigationReceipt,
    NavigationRecord,
    NavigationSaveState,
    StructureOverride,
)
from app.persistence.preparation_models import HostReviewRequest
from app.persistence.room_models import RoomEvent
from app.rooms.service import require


class ModuleNavigationService:
    def __init__(self, agents):
        self.agents, self.rooms = agents, agents.rooms

    async def state(self, session, room_id):
        row = await session.get(NavigationRecord, room_id)
        return RoomModuleNavigationState.model_validate(row.document) if row else None

    async def persist(self, session, state):
        state.updated_at = utc_now()
        state = RoomModuleNavigationState.model_validate(state.model_dump())
        row = await session.get(NavigationRecord, state.room_id)
        if row:
            row.document = state.model_dump(mode="json")
        else:
            session.add(
                NavigationRecord(room_id=state.room_id, document=state.model_dump(mode="json"))
            )

    async def snapshot(self, session, state):
        row = await session.get(ApprovedStructure, state.structure_snapshot_id)
        require(row, "module_structure_missing：批准快照不存在，请加载匹配版本")
        try:
            snapshot = StructureSnapshot.model_validate(row.document)
        except ValueError:
            require(False, "module_structure_missing：批准快照校验失败")
        ir = self.agents.structure.repository.get(state.structure_version)
        require(
            snapshot.approved
            and ir
            and snapshot.source_hash == state.module_source_hash
            and ir.source_hash == state.module_source_hash
            and snapshot.structure_version == state.structure_version
            and snapshot.source_id == ir.source_id,
            "module_structure_missing：来源或版本不匹配",
        )
        require(
            {n.node_id for n in snapshot.nodes} == {n.node_id for n in ir.nodes},
            "module_structure_missing：节点集合不匹配",
        )
        nodes = {n.node_id: n for n in snapshot.nodes}
        referenced_nodes = {
            *state.visited_scene_node_ids,
            *state.selected_node_ids,
            *([state.previous_scene_node_id] if state.previous_scene_node_id else []),
        }
        require(
            referenced_nodes <= set(nodes)
            and set(state.selected_block_ids) <= {b.block_id for b in ir.blocks},
            "module_structure_missing：存档节点或区块引用不可用",
        )
        require(
            state.current_scene_node_id in nodes
            and nodes[state.current_scene_node_id].included
            and nodes[state.current_scene_node_id].approved_type == "scene",
            "module_structure_missing：当前场景不存在",
        )
        entities = {
            e.source_entity_id for e in await self.agents.entities.rows(session, state.room_id)
        }
        require(
            all(b.entity_id in entities for b in snapshot.entity_bindings),
            "module_structure_missing：实体绑定不可用",
        )
        require(
            set(state.available_transition_ids) <= {t.transition_id for t in snapshot.transitions},
            "module_structure_missing：转换引用不可用",
        )
        return snapshot, ir

    async def require_available(self, session, room_id):
        state = await self.state(session, room_id)
        if state:
            await self.snapshot(session, state)
            if state.module_structure_missing:
                state.module_structure_missing = False
                await self.persist(session, state)
        return state

    async def bind(self, session, room, preparation):
        override = await session.get(StructureOverride, preparation.id)
        require(
            override and override.approved_snapshot_id,
            "请先校正并批准文档结构和 initial scene",
            422,
        )
        row = await session.get(ApprovedStructure, override.approved_snapshot_id)
        require(row, "module_structure_missing", 422)
        snapshot = StructureSnapshot.model_validate(row.document)
        head = self.agents.knowledge.repository.source(snapshot.source_id)
        require(
            snapshot.approved
            and snapshot.preparation_version == preparation.version
            and head
            and head.source_hash == snapshot.source_hash == preparation.source_hash,
            "stale structure：结构或准备版本已改变，不能绑定新房间",
            422,
        )
        state = RoomModuleNavigationState(
            room_id=room.id,
            structure_snapshot_id=snapshot.snapshot_id,
            structure_version=snapshot.structure_version,
            module_source_hash=snapshot.source_hash,
            current_scene_node_id=snapshot.initial_scene_node_id,
            visited_scene_node_ids=[snapshot.initial_scene_node_id],
        )
        await self.persist(session, state)
        await session.flush()
        await self.refresh(session, room, state, snapshot)
        await self.project(session, room, state, snapshot)
        state.updated_event_seq = room.revision
        await self.persist(session, state)
        self.rooms.append(
            session,
            room,
            "module.navigation_bound",
            room.host_member_id,
            {
                "structure_snapshot_id": snapshot.snapshot_id,
                "current_scene_node_id": state.current_scene_node_id,
            },
            "host_only",
        )
        return state

    async def check_cycle(self, session, room, cycle):
        state = await self.require_available(session, room.id)
        if state:
            expected = cycle.state.get("navigation_revision")
            require(
                expected is None or expected == state.navigation_revision,
                "navigation_revision_conflict：场景已改变，请取消旧回合后重试",
            )
            cycle.state = {
                **cycle.state,
                "navigation_revision": state.navigation_revision,
                "current_scene_node_id": state.current_scene_node_id,
                "structure_snapshot_id": state.structure_snapshot_id,
                "module_source_hash": state.module_source_hash,
            }
        return state

    async def conditions(self, session, room, state, transition):
        if not set(transition.required_revealed_entity_ids) <= set(state.revealed_entity_ids):
            return False
        event_types = set(
            await session.scalars(select(RoomEvent.type).where(RoomEvent.room_id == room.id))
        )
        return set(transition.required_event_types) <= event_types

    async def refresh(self, session, room, state, snapshot):
        rows = await self.agents.entities.rows(session, room.id)
        bound = {
            b.entity_id
            for b in snapshot.entity_bindings
            if b.node_id == state.current_scene_node_id
        }
        state.active_npc_entity_ids = [
            e.source_entity_id
            for e in rows
            if e.source_entity_id in bound and e.entity_type == "npc"
        ]
        state.active_location_entity_ids = [
            e.source_entity_id
            for e in rows
            if e.source_entity_id in bound and e.entity_type == "location"
        ]
        state.revealed_entity_ids = [e.source_entity_id for e in rows if e.state != "hidden"]
        state.available_transition_ids = []
        for transition in snapshot.transitions:
            if (
                transition.source_scene_node_id == state.current_scene_node_id
                and transition.approved
                and transition.transition_type != "host_only"
                and await self.conditions(session, room, state, transition)
            ):
                state.available_transition_ids.append(transition.transition_id)

    async def public_scene(self, session, room):
        state = await self.state(session, room.id)
        if not state:
            return {
                "title": room.session_state.get("scene_title", ""),
                "summary": room.session_state.get("scene_summary", ""),
            }
        # Public projection survives missing knowledge. It contains no raw node identifier.
        return {
            "title": room.session_state.get("scene_title", ""),
            "summary": room.session_state.get("scene_summary", ""),
            "entities": await self.agents.entities.public(session, room.id),
        }

    async def project(self, session, room, state, snapshot):
        node = next(n for n in snapshot.nodes if n.node_id == state.current_scene_node_id)
        rows = await self.agents.entities.rows(session, room.id)
        scene_entity = next(
            (
                e
                for e in rows
                if e.entity_type == "scene"
                and any(
                    b.entity_id == e.source_entity_id and b.node_id == node.node_id
                    for b in snapshot.entity_bindings
                )
            ),
            None,
        )
        public_id = scene_entity.source_entity_id if scene_entity else "current-public-scene"
        if scene_entity and scene_entity.state == "hidden":
            await self.agents.entities.reveal(
                session,
                room,
                scene_entity.source_entity_id,
                room.host_member_id,
                host_override=True,
            )
        prepared = await self.agents.entities.binding(session, room.id)
        if scene_entity:
            prepared.current_scene = scene_entity.source_entity_id
        room.session_state = {
            **room.session_state,
            "scene_title": node.public_title,
            "scene_summary": node.public_summary,
        }
        shell = await self.agents.module(session, room.id)
        shell.document = {
            **shell.document,
            "initial_scene": public_id,
            "scenes": [
                {
                    "id": public_id,
                    "title": node.public_title,
                    "public_description": node.public_summary,
                    "keeper_notes": "",
                }
            ],
        }
        shell.state = {**shell.state, "scene_id": public_id}

    async def transition(self, session, room, request, run=None, host=False, reviewed=False):
        require(room.status in {"lobby", "running", "paused"}, "房间已结束，不能转换场景")
        state = await self.require_available(session, room.id)
        require(state, "module_structure_missing：房间没有结构导航", 422)
        request = TransitionRequest.model_validate(request)
        previous = await session.get(NavigationReceipt, (room.id, request.request_id))
        fingerprint = content_hash(request.model_dump())
        if previous:
            require(previous.request_hash == fingerprint, "transition_idempotency_conflict")
            if not reviewed or not previous.result.get("pending"):
                return previous.result
        require(
            request.expected_revision == state.navigation_revision,
            "navigation_revision_conflict：请刷新当前场景",
        )
        snapshot, _ = await self.snapshot(session, state)
        target = next(
            (n for n in snapshot.nodes if n.node_id == request.target_scene_node_id), None
        )
        require(
            target and target.included and target.approved_type == "scene",
            "invalid_scene_node",
            422,
        )
        await self.refresh(session, room, state, snapshot)
        if run:
            cycle = await session.get(AgentCycle, run.cycle_id)
            await self.check_cycle(session, room, cycle)
        legal = next(
            (
                t
                for t in snapshot.transitions
                if t.source_scene_node_id == state.current_scene_node_id
                and t.target_scene_node_id == target.node_id
                and t.transition_id in state.available_transition_ids
            ),
            None,
        )
        if target.node_id == state.current_scene_node_id:
            result = {
                "unchanged": True,
                "current_scene_node_id": target.node_id,
                "navigation_revision": state.navigation_revision,
            }
        elif not host and not legal:
            require(run, "无批准转换，请由主机提出一次性转换")
            pending_id = cycle.state.get("pending_check_id")
            pending = await session.get(CheckRecord, pending_id) if pending_id else None
            require(not pending or pending.status != "pending", "已有检定等待，不能并行审阅")
            require(
                not await session.scalar(
                    select(HostReviewRequest).where(HostReviewRequest.cycle_id == cycle.id)
                ),
                "每个 cycle 最多一次主机审阅",
            )
            review = HostReviewRequest(
                id=str(uuid4()),
                room_id=room.id,
                cycle_id=cycle.id,
                agent_run_id=run.id,
                request_type="scene_transition",
                status="pending",
                document={
                    "entity_type": "scene",
                    "entity_id": None,
                    "proposed_title": target.public_title,
                    "proposed_public_summary": target.public_summary,
                    "keeper_reason": "当前场景没有可执行的批准转换，或条件尚未满足",
                    "evidence_ids": [],
                    "related_entity_ids": [],
                    "evidence": [],
                    "host_response": "",
                    "applied": False,
                    "navigation_request": request.model_dump(),
                },
            )
            session.add(review)
            state.pending_transition, state.pending_review_id = request, review.id
            cycle.state = {
                **cycle.state,
                "pending_review_id": review.id,
                "review_count": 1,
                "transition_request": request.model_dump(),
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
                {"cycle_id": cycle.id, "text": "[主机审阅] 等待主机确认场景转换。"},
            )
            result = {
                "pending": True,
                "review_id": review.id,
                "code": "transition_waiting_host_review",
            }
        else:
            source_id = state.current_scene_node_id
            state.previous_scene_node_id, state.current_scene_node_id = source_id, target.node_id
            if target.node_id not in state.visited_scene_node_ids:
                state.visited_scene_node_ids.append(target.node_id)
            state.navigation_revision += 1
            state.pending_transition, state.pending_review_id = None, None
            state.selected_node_ids, state.selected_block_ids = [], []
            await self.project(session, room, state, snapshot)
            await self.refresh(session, room, state, snapshot)
            self.rooms.append(
                session,
                room,
                "scene.updated",
                room.host_member_id if host else run.actor_member_id,
                {
                    "scene_title": target.public_title,
                    "scene_summary": target.public_summary,
                    "cycle_id": run.cycle_id if run else None,
                },
            )
            event = self.rooms.append(
                session,
                room,
                "module.scene_transition",
                room.host_member_id,
                {
                    "source": source_id,
                    "target": target.node_id,
                    "navigation_revision": state.navigation_revision,
                    "transition_id": legal.transition_id if legal else None,
                    "host_override": host,
                },
                "host_only",
            )
            state.updated_event_seq = event.seq
            result = {
                "current_scene_node_id": target.node_id,
                "navigation_revision": state.navigation_revision,
                "event_seq": event.seq,
            }
            if run:
                cycle.state = {
                    **cycle.state,
                    "navigation_revision": state.navigation_revision,
                    "current_scene_node_id": target.node_id,
                    "transition_request": request.model_dump(),
                    "transition_result": result,
                }
            for memory in await session.scalars(
                select(AgentMemory).where(
                    AgentMemory.room_id == room.id,
                    AgentMemory.kind == "summary",
                    AgentMemory.scope == "keeper_only",
                    AgentMemory.active.is_(True),
                )
            ):
                try:
                    data = json.loads(memory.content)
                except ValueError:
                    data = {"content": memory.content}
                memory.content = json.dumps(
                    {
                        **data,
                        "current_scene_node_id": target.node_id,
                        "heading_path": target.heading_path,
                    },
                    ensure_ascii=False,
                )
        await self.persist(session, state)
        if previous:
            previous.result = result
        else:
            session.add(
                NavigationReceipt(
                    room_id=room.id,
                    request_id=request.request_id,
                    request_hash=fingerprint,
                    result=result,
                )
            )
        return result

    async def save(self, session, room, snapshot):
        state = await self.state(session, room.id)
        if state:
            approved, _ = await self.snapshot(session, state)
            await self.refresh(session, room, state, approved)
            session.add(
                NavigationSaveState(snapshot_id=snapshot.id, document=state.model_dump(mode="json"))
            )

    async def load(self, session, room, snapshot):
        saved = await session.get(NavigationSaveState, snapshot.id)
        current = await self.state(session, room.id)
        require(saved or not current, "旧存档没有结构导航，请使用绑定结构后的存档")
        if saved:
            from app.rooms.service import RoomError

            state = RoomModuleNavigationState.model_validate(saved.document)
            try:
                approved, _ = await self.snapshot(session, state)
                await self.refresh(session, room, state, approved)
                state.module_structure_missing = False
            except RoomError:
                state.module_structure_missing = True
            await self.persist(session, state)

    async def reconcile(self, session, room):
        state = await self.state(session, room.id)
        if state and not state.module_structure_missing:
            snapshot, _ = await self.snapshot(session, state)
            await self.project(session, room, state, snapshot)
        return state
