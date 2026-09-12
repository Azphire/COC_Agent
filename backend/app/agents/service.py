"""Agent domain commands sharing multiplayer locks, transactions and event delivery."""

from uuid import uuid4

from sqlalchemy import select

from app.agents import schemas as s
from app.agents.modules import Module, content_hash, load_modules, public_module
from app.agents.security import scrub
from app.domain.character import utc_now
from app.memory.service import memories, memory_view
from app.persistence.agent_models import (
    AgentCycle,
    AgentMemory,
    AgentRun,
    AgentSaveState,
    CheckRecord,
    ModuleSnapshot,
    ProfileRecord,
    RoomAgentBinding,
)
from app.persistence.knowledge_models import AgentModelCall
from app.persistence.room_models import RoomEvent, RoomMember
from app.rooms.service import event_view, iso_utc, require
from app.rules.compound import preserves_ordinary_condition

ACTIVE = ("running", "waiting_for_roll", "waiting_for_review", "failed")


class AgentService:
    def __init__(self, rooms, model):
        self.rooms, self.settings, self.model = rooms, rooms.settings, model
        self.modules = load_modules()
        self.runtime = None
        from app.knowledge.service import KnowledgeService

        self.knowledge = KnowledgeService(self)
        from app.preparation.room_service import RoomEntityService
        from app.preparation.service import PreparationService

        self.preparation = PreparationService(self)
        self.entities = RoomEntityService(self)
        from app.module_ir.context import ModuleContextResolver
        from app.module_ir.navigation import ModuleNavigationService
        from app.module_ir.service import ModuleStructureService

        self.structure = ModuleStructureService(self)
        self.navigation = ModuleNavigationService(self)
        self.module_context = ModuleContextResolver(self)
        from app.agents.adjudication import ActionAdjudicationService

        self.adjudication = ActionAdjudicationService(self)
        from app.memory.recovery import SummaryRecoveryService

        self.summary_recovery = SummaryRecoveryService(self)
        from app.rooms.sanity_service import SanityService

        self.sanity = SanityService(self)
        from app.agents.settlement import CheckSettlementService

        self.settlement = CheckSettlementService(self)
        from app.agents.compound import CompoundCheckService

        self.compound = CompoundCheckService(self)
        from app.rooms.combat_service import CombatService

        self.combat = CombatService(self)

    async def sanitize(self, session, room, value):
        members = await self.rooms.members(session, room)
        hashes = {m.token_hash for m in members if m.token_hash} | {room.invite_hash}
        secrets = [
            self.settings.host_admin_token.get_secret_value(),
            self.settings.model_api_key.get_secret_value(),
        ]
        return scrub(value, secrets, hashes)

    async def module(self, session, room_id):
        return await session.scalar(select(ModuleSnapshot).where(ModuleSnapshot.room_id == room_id))

    async def bindings(self, session, room_id):
        return list(
            await session.scalars(
                select(RoomAgentBinding)
                .where(RoomAgentBinding.room_id == room_id, RoomAgentBinding.enabled.is_(True))
                .order_by(RoomAgentBinding.created_at, RoomAgentBinding.id)
            )
        )

    async def cycle(self, session, room_id, active=False):
        query = select(AgentCycle).where(AgentCycle.room_id == room_id)
        if active:
            query = query.where(AgentCycle.status.in_(ACTIVE))
        else:
            current = await self.cycle(session, room_id, active=True)
            if current:
                return current
            query = query.where(AgentCycle.status.not_in(["queued", "queued_action", "suspended"]))
        return await session.scalar(query.order_by(AgentCycle.created_at.desc()).limit(1))

    async def mutate(self, room_id, callback):
        async with self.rooms.lock(room_id):
            async with self.rooms.transaction() as session:
                room = await self.rooms.room(session, room_id)
                require(room.status != "ended", "房间已结束")
                before = room.revision
                result = await callback(session, room)
                await session.flush()
                events = list(
                    await session.scalars(
                        select(RoomEvent)
                        .where(RoomEvent.room_id == room.id, RoomEvent.seq > before)
                        .order_by(RoomEvent.seq)
                    )
                )
            await self.rooms.broadcast(room_id, events)
            return result

    async def check_views(self, session, room, identity, cycle_id=None):
        query = select(CheckRecord).where(CheckRecord.room_id == room.id)
        if cycle_id:
            query = query.where(CheckRecord.cycle_id == cycle_id)
        records = await session.scalars(query)
        views = [
            (
                {**r.document, **self.check_public(r.document), "sanity": r.document["sanity"]}
                if r.document.get("sanity")
                else {**r.document, **self.check_public(r.document)}
            )
            if identity.is_host
            else self.check_public(r.document)
            for r in records
            if identity.is_host
            or r.document["visibility"] == "public"
            or (
                r.document["visibility"] == "actor_and_host"
                and r.target_member_id == identity.member_id
            )
        ]

        for view in views:
            if view.get("opposed"):
                progress = view["compound"]
                record = await session.get(CheckRecord, view["id"])
                for i, side in enumerate(progress["participants"]):
                    if identity.is_host or side["member_id"] == identity.member_id:
                        side["options"] = (
                            await self.compound.side_options(
                                session, room, record.document["compound"]["participants"][i]
                            )
                            if record.status == "pending"
                            and progress["stage"] == "choice"
                            and progress["choice_index"] == i
                            else {"luck": [], "push": False}
                        )
                continue
            if view.get("settlement") and (
                identity.is_host or view["target_member_id"] == identity.member_id
            ):
                record = await session.get(CheckRecord, view["id"])
                view["options"] = await self.settlement.options(session, room, record.document)
                if view["status"] != "pending" or view["settlement"]["stage"] != "choice":
                    view["options"] = {"luck": [], "push": False}
        return views

    @staticmethod
    def check_public(document):
        from copy import deepcopy

        document = deepcopy(document)
        for side in (document.get("compound") or {}).get("participants", []):
            side.pop("source", None)
            side.pop("source_page", None)
        if document.get("sanity"):
            from app.rooms.sanity_service import sanity_public

            return sanity_public(document)
        from app.rules.display import check_display

        display = check_display(document)
        return {
            **{
                key: value
                for key, value in document.items()
                if key
                not in {
                    "clue_id",
                    "policy_fingerprint",
                    "policy_target_id",
                    "attempt_purpose",
                    "attempt_method",
                    "alternative_basis",
                }
            },
            **display,
            "name": display["display_name"],
        }

    async def view(self, session, room, identity):
        module = await self.module(session, room.id)
        cycle = await self.cycle(session, room.id)
        checks = await self.check_views(session, room, identity)
        result = {
            "knowledge": await self.knowledge.view(session, room.id),
            "module": public_module(module) if module else None,
            "enabled": module.enabled if module else False,
            "checks": checks,
            "cycle": None,
            "bindings": [],
            "public_entities": await self.entities.public(session, room.id),
        }
        if cycle:
            result["cycle"] = {
                "id": cycle.id,
                "status": cycle.status,
                "current_node": cycle.state["current_node"],
                "safe_error": cycle.state.get("safe_error"),
                "wait_reason": cycle.state.get("wait_reason"),
                "requires_clarification": cycle.state.get("requires_clarification", False),
                "clarification_question": cycle.state.get("clarification_question"),
                "clarification_event_seq": cycle.state.get("clarification_event_seq"),
            }
            if identity.is_host:
                result["cycle"]["state"] = cycle.state
        for binding in await self.bindings(session, room.id):
            profile = await session.get(ProfileRecord, binding.profile_id)
            result["bindings"].append(
                {
                    "id": binding.id,
                    "member_id": binding.member_id,
                    "profile_id": binding.profile_id,
                    "role": profile.role,
                    "name": profile.document["name"],
                    "status": binding.status,
                    **(
                        {"last_consumed_event_seq": binding.last_consumed_event_seq}
                        if identity.is_host
                        else {}
                    ),
                }
            )
        if identity.is_host and module:
            result["keeper_module"] = module.document
            result["host_entities"] = await self.entities.host(session, room.id)
        prepared = await self.entities.binding(session, room.id)
        if prepared:
            result["preparation"] = {
                "id": prepared.preparation_id,
                "version": prepared.preparation_version,
                "source_hash": prepared.source_hash,
            }
        navigation = await self.navigation.state(session, room.id)
        result["conversation_targets"] = [
            e
            for e in result["public_entities"]
            if e["type"] == "npc"
            and e.get("fact_scope") == "current_scene"
            and (not navigation or e["id"] in navigation.active_npc_entity_ids)
        ]
        return result

    async def get(self, room_id, token, kind, target=None):
        async with self.rooms.database.sessions() as session:
            room = await self.rooms.room(session, room_id)
            identity = await self.rooms.identity(session, room, token)
            if kind in {"plan", "validation", "teammate_behavior", "summary_status"}:
                require(identity.is_host, "仅主机可查看内部行动和恢复状态", 403)
                from app.agents.adjudication_schemas import AdjudicationRecord
                from app.persistence.adjudication_models import (
                    ActionPlanRecord,
                    AgentBehaviorRecord,
                    SummaryRecoveryRecord,
                )

                if kind in {"plan", "validation"}:
                    record = await session.get(ActionPlanRecord, str(target))
                    require(record and record.room_id == room.id, "计划不存在", 404)
                    document = AdjudicationRecord.model_validate(record.document)
                    return (
                        document.plan.model_dump(mode="json")
                        if kind == "plan"
                        else document.model_dump(mode="json")
                    )
                model = (
                    AgentBehaviorRecord if kind == "teammate_behavior" else SummaryRecoveryRecord
                )
                records = await session.scalars(select(model).where(model.room_id == room.id))
                return [
                    {
                        "member_id" if kind == "teammate_behavior" else "profile_id": r.member_id
                        if kind == "teammate_behavior"
                        else r.profile_id,
                        **r.document,
                    }
                    for r in records
                ]
            if kind == "checks":
                return await self.check_views(session, room, identity)
            if kind in {"runs", "run", "memories", "config"}:
                require(identity.is_host, "仅主机可查看 Agent 配置和调试信息", 403)
            if kind == "memories":
                return [memory_view(m) for m in await memories(session, room.id, host=True)]
            if kind in {"runs", "run"}:
                query = (
                    select(AgentRun)
                    .where(AgentRun.room_id == room.id)
                    .order_by(AgentRun.created_at)
                )
                if target:
                    query = query.where(AgentRun.id == str(target))
                runs = list(await session.scalars(query))
                if target:
                    require(runs, "运行记录不存在", 404)
                views = [
                    {
                        c.name: (
                            iso_utc(getattr(run, c.name))
                            if c.name.endswith("_at") and getattr(run, c.name)
                            else getattr(run, c.name)
                        )
                        for c in run.__table__.columns
                    }
                    for run in runs
                ]
                for view in views:
                    view["model_calls"] = [
                        c.document
                        for c in await session.scalars(
                            select(AgentModelCall).where(AgentModelCall.run_id == view["id"])
                        )
                    ]
                return views[0] if target else views
            view = await self.view(session, room, identity)
            return view["cycle"] if kind == "cycle" else view

    async def ensure_config(self, session, room):
        await self.navigation.require_available(session, room.id)
        await self.knowledge.require_available(session, room.id)
        module = await self.module(session, room.id)
        require(module and module.enabled, "请先选择测试模组并启用 Agent")
        require(not module.state.get("completed"), "测试模组已结束")
        bindings = await self.bindings(session, room.id)
        require(any(b.member_id == room.host_member_id for b in bindings), "请先配置 AI KP")
        members = await self.rooms.members(session, room)
        slots = await self.rooms.slots(session, room)
        for member in members:
            if member.active and member.controller_type == "agent":
                require(
                    any(b.member_id == member.id for b in bindings),
                    "存在未绑定 Profile 的 Agent 席位",
                )
        for binding in bindings:
            member = next((m for m in members if m.id == binding.member_id), None)
            require(member and member.active, "Agent 绑定成员已失活")
            if binding.member_id != room.host_member_id:
                require(
                    any(slot.member_id == binding.member_id for slot in slots),
                    "AI 队友尚未分配角色",
                )
        return bindings

    async def apply(self, session, room, identity, action, body, target):
        action = action.removeprefix("agent.")
        if action not in {"action", "check.roll", "sanity.roll", "check.choice", "check.push_roll"}:
            require(identity.is_host, "仅主机可以执行此操作", 403)
        if action == "check.rules":
            require(body.expected_revision == room.revision, "房间版本已变化")
            require(not await self.cycle(session, room.id, active=True), "请在无活动回合时配置")
            room.session_state = {**room.session_state, "luck_spending": body.luck_spending}
            self.rooms.append(
                session,
                room,
                "check.rules_changed",
                identity.member_id,
                {"luck_spending": body.luck_spending},
            )
            return {}
        if action in {"check.choice", "check.push_review", "check.push_roll", "check.handled"}:
            record = await session.get(CheckRecord, target)
            if record and record.document.get("opposed"):
                require(action == "check.choice", "对抗不能孤注一掷", 422)
                participant = await self.compound.authorize(
                    session,
                    room,
                    identity,
                    record,
                    str(body.participant_id) if body.participant_id else None,
                )
                await self.compound.choice(session, room, record, participant, body)
                return {"check": self.check_public(record.document)}
            if action == "check.choice" and body.participant_id:
                require(
                    record and str(body.participant_id) == record.target_member_id,
                    "不能选择其他参与者的结果",
                    403,
                )
            await self.settlement.authorize(
                session, room, identity, record, action in {"check.push_review", "check.handled"}
            )
            if action == "check.choice":
                await self.settlement.choice(session, room, record, body)
            elif action == "check.push_review":
                await self.settlement.review(session, room, record, body)
            elif action == "check.push_roll":
                await self.settlement.push_roll(session, room, record)
            else:
                await self.settlement.handled(session, room, record, body)
            return {"check": self.check_public(record.document)}
        if action == "sanity.request":
            record = await self.sanity.request(session, room, body)
            return {"check": record.document}
        if action == "sanity.review":
            cycle = await self.cycle(session, room.id, active=True)
            await self.sanity.encounters.review(session, room, cycle, body)
            return {}
        if action == "sanity.manage":
            await self.sanity.manage(session, room, body)
            return {}
        if action == "resource.correct":
            await self.sanity.correct(session, room, body)
            return {}
        cycle = await self.cycle(session, room.id, active=True)
        if action in {"module", "binding", "unbind", "config", "knowledge"}:
            require(
                room.status in {"lobby", "paused"} and cycle is None,
                "请在无活动回合的大厅或暂停状态配置",
            )
        if action == "knowledge":
            await self.knowledge.bind(session, room, body, identity.member_id)
        elif action == "module":
            require(body.module_id in self.modules, "模组不存在", 404)
            require(
                await self.module(session, room.id) is None,
                "房间已绑定模组，请新建房间体验另一模组",
            )
            module = self.modules[body.module_id]
            document = module.model_dump(mode="json")
            record = ModuleSnapshot(
                id=str(uuid4()),
                room_id=room.id,
                document=document,
                content_hash=content_hash(document),
                state={
                    "scene_id": module.initial_scene,
                    "revealed_clues": [c.id for c in module.clues if c.visibility == "public"],
                    "completed": False,
                },
                enabled=True,
            )
            session.add(record)
            scene = next(s for s in module.scenes if s.id == module.initial_scene)
            room.session_state = {
                **room.session_state,
                "scene_title": scene.title,
                "scene_summary": scene.public_description,
            }
            self.rooms.append(
                session,
                room,
                "module.bound",
                identity.member_id,
                {
                    "module_id": module.id,
                    "title": module.title,
                    "introduction": module.public_introduction,
                },
            )
            self.rooms.append(
                session,
                room,
                "scene.updated",
                identity.member_id,
                {
                    "scene_id": scene.id,
                    "scene_title": scene.title,
                    "scene_summary": scene.public_description,
                    "initialization": True,
                },
            )
        elif action == "config":
            module = await self.module(session, room.id)
            require(module, "请先选择模组")
            module.enabled = body.enabled
            self.rooms.append(
                session, room, "agent.configured", identity.member_id, {"enabled": body.enabled}
            )
        elif action == "binding":
            profile = await session.get(ProfileRecord, str(body.profile_id))
            member = await session.get(RoomMember, str(body.member_id))
            require(
                profile is not None
                and member is not None
                and member.room_id == room.id
                and member.active,
                "Profile 或房间成员不存在",
                404,
            )
            require(
                (profile.role == "keeper" and member.id == room.host_member_id)
                or (
                    profile.role == "investigator"
                    and member.role == "player"
                    and member.controller_type == "agent"
                ),
                "Profile 类型与席位不匹配",
                422,
            )
            bindings = await self.bindings(session, room.id)
            require(not any(b.member_id == member.id for b in bindings), "此席位已有启用的 Profile")
            # A profile represents one mind within a room, so private memory cannot alias seats.
            require(
                not any(b.profile_id == profile.id for b in bindings),
                "同一房间中 Profile 只能绑定一次",
            )
            if profile.role == "investigator":
                require(
                    any(
                        slot.member_id == member.id
                        for slot in await self.rooms.slots(session, room)
                    ),
                    "请先为 AI 队友分配角色",
                )
            binding = RoomAgentBinding(
                id=str(uuid4()),
                room_id=room.id,
                member_id=member.id,
                profile_id=profile.id,
                enabled=True,
                status="idle",
                last_consumed_event_seq=0,
            )
            session.add(binding)
            self.rooms.append(
                session,
                room,
                "agent.bound",
                member.id,
                {"binding_id": binding.id, "role": profile.role, "name": profile.document["name"]},
            )
        elif action == "unbind":
            binding = await session.get(RoomAgentBinding, target)
            require(binding and binding.room_id == room.id, "绑定不存在", 404)
            binding.enabled, binding.status = False, "disabled"
            self.rooms.append(
                session, room, "agent.unbound", binding.member_id, {"binding_id": binding.id}
            )
        elif action == "action":
            require(room.status == "running", "请先开始或恢复游戏")
            actor = str(body.actor_member_id) if body.actor_member_id else identity.member_id
            member = await session.get(RoomMember, actor)
            require(
                member
                and member.room_id == room.id
                and member.active
                and member.role == "player"
                and member.controller_type == "human",
                "行动必须来自活动真人调查员",
                403,
            )
            require(
                actor == identity.member_id
                or (identity.is_host and member.access_type == "host_managed"),
                "只能提交自己的行动",
                403,
            )
            safe_body = await self.sanitize(session, room, body.model_dump(mode="json"))
            fingerprint = content_hash(safe_body)
            previous = await session.scalar(
                select(RoomEvent).where(
                    RoomEvent.room_id == room.id,
                    RoomEvent.actor_member_id == actor,
                    RoomEvent.client_request_id == str(body.client_request_id),
                )
            )
            if previous:
                require(previous.request_hash == fingerprint, "请求 ID 已用于不同内容")
                return {"event": event_view(previous)}
            rule_question = body.category == "rule_question"
            require(
                not rule_question or not (body.target_entity_id or body.clarification_event_seq),
                "规则提问不指定人物或行动澄清目标",
                422,
            )
            if body.clarification_event_seq:
                clarification = await session.get(
                    RoomEvent, (room.id, body.clarification_event_seq)
                )
                require(
                    clarification
                    and clarification.type == "action.clarification_requested"
                    and clarification.payload.get("actor_member_id") == actor,
                    "澄清请求不存在或不属于此行动者",
                    403,
                )
            if body.target_entity_id:
                visible = await self.entities.public(session, room.id)
                visible_ids = {e["id"] for e in visible}
                if not await self.entities.binding(session, room.id):
                    module = await self.module(session, room.id)
                    visible_ids |= set(module.state["revealed_clues"])
                    visible_ids |= {n["id"] for n in module.document["npcs"]}
                require(body.target_entity_id in visible_ids, "行动目标不可见", 403)
            bindings = await self.ensure_config(session, room)
            if not rule_question:
                from uuid import UUID

                from app.rooms.schemas import SessionStateV1

                actor_slot = next(
                    (s for s in await self.rooms.slots(session, room) if s.member_id == actor), None
                )
                if actor_slot:
                    current = SessionStateV1.model_validate(room.session_state).characters[
                        UUID(actor_slot.id)
                    ]
                    require(
                        current.sanity.kind != "permanent"
                        and current.sanity.phase not in {"bout", "awaiting_symptom"},
                        "疯狂发作期间请由主机处理角色行动",
                    )
                require(
                    any(slot.member_id == actor for slot in await self.rooms.slots(session, room)),
                    "请先绑定角色",
                )
            cycle_id = str(uuid4())
            event = self.rooms.append(
                session,
                room,
                "rules.question" if rule_question else "action.submitted",
                actor,
                {
                    "text": safe_body["text"],
                    "category": body.category,
                    "cycle_id": cycle_id,
                    "target_entity_id": body.target_entity_id,
                    "clarification_event_seq": body.clarification_event_seq,
                },
                request_id=str(body.client_request_id),
                request_hash=fingerprint,
            )
            state = s.AgentCycleState(
                request_category=body.category,
                cycle_id=cycle_id,
                room_id=room.id,
                triggering_member_id=actor,
                triggering_event_seq=event.seq,
                current_node="collect_context",
                keeper_run_id=None,
                pending_check_id=None,
                wait_reason=None,
                pending_review_id=None,
                approved_entity_ids_used=[],
                proposed_entity_ids=[],
                revealed_entity_ids=[],
                scene_transition=None,
                review_count=0,
                review_result=None,
                deferred_tools=[],
                tool_results=[],
                teammate_queue=[b.id for b in bindings if b.member_id != room.host_member_id],
                completed_teammate_ids=[],
                call_count=0,
                tool_count=0,
                status="running",
                safe_error=None,
            )
            parent = cycle
            state["parent_cycle_id"] = parent.id if parent else None
            state["origin"] = "human"
            if not rule_question and self.combat.route(room, body.text):
                state.update(combat_flow=True, combat_actor_id=actor)
            cycle = AgentCycle(
                id=cycle_id, room_id=room.id, status="queued" if parent else "running", state=state
            )
            session.add(cycle)
            if not parent:
                self.cycle_event(session, room, cycle)
            return {"event": event_view(event)}
        elif action == "behavior.reset":
            require(cycle is None, "请在回合结束后重置队友状态")
            from app.agents.adjudication_schemas import BehaviorState
            from app.persistence.adjudication_models import AgentBehaviorRecord

            row = await session.get(AgentBehaviorRecord, (room.id, target))
            require(row, "队友状态不存在", 404)
            row.document = BehaviorState().model_dump(mode="json")
            self.rooms.append(
                session,
                room,
                "agent.behavior_reset",
                room.host_member_id,
                {"member_id": target},
                "host_only",
            )
        elif action in {"check.roll", "sanity.roll"}:
            check = await session.get(CheckRecord, target)
            require(check and check.room_id == room.id, "检定不存在", 404)
            if check.document.get("opposed"):
                require(action == "check.roll", "对抗不能走 SAN 接口", 422)
                participant = await self.compound.authorize(
                    session,
                    room,
                    identity,
                    check,
                    str(body.participant_id) if body.participant_id else None,
                )
                await self.compound.advance(session, room, check, participant)
                return {"check": self.check_public(check.document)}
            if check.document.get("combined"):
                await self.settlement.authorize(session, room, identity, check)
            require(
                identity.is_host or check.target_member_id == identity.member_id,
                "只能掷自己角色的检定",
                403,
            )
            require(room.status == "running", "请先恢复游戏")
            from app.agents.conversation import ensure_no_earlier_message

            if check.status == "pending":
                await ensure_no_earlier_message(session, room, cycle)
            if check.document.get("sanity"):
                require(action == "sanity.roll", "SAN 检定须明确确认当前阶段", 422)
                require(
                    check.status == "resolved"
                    or cycle
                    and cycle.id == check.cycle_id
                    and cycle.status == "waiting_for_roll",
                    "SAN 不属于等待中的回合",
                )
                await self.sanity.roll(session, room, check, body.expected_stage)
                return {
                    "check": check.document
                    if identity.is_host
                    else self.check_public(check.document)
                }
            require(action == "check.roll", "普通检定不能走 SAN 接口", 422)
            if check.status == "resolved":
                return {
                    "check": check.document
                    if identity.is_host
                    else self.check_public(check.document)
                }
            require(
                cycle and check.cycle_id == cycle.id and cycle.status == "waiting_for_roll",
                "检定不属于等待中的回合",
            )
            await self.resolve_check(session, room, check, automatic=False)
            return {
                "check": check.document if identity.is_host else self.check_public(check.document)
            }
        elif action in {"cancel", "check.cancel"}:
            require(cycle is not None, "没有活动回合")
            for pending in await session.scalars(
                select(CheckRecord).where(
                    CheckRecord.cycle_id == cycle.id, CheckRecord.status == "pending"
                )
            ):
                progress = pending.document.get("sanity")
                settlement = pending.document.get("settlement") or {}
                if pending.document.get("compound"):
                    from app.agents.conversation import can_withdraw

                    require(
                        await can_withdraw(session, room, pending),
                        "复合检定已经固定骰点，须完成结算或读档",
                    )
                require(
                    not settlement
                    and not any(
                        s.get("dice")
                        for s in (pending.document.get("compound") or {}).get("participants", [])
                    ),
                    "已掷出的普通检定须选择接受或完成结算；回退请读档",
                )
                require(
                    not progress or progress.get("after") is None,
                    "已扣减的 SAN 遭遇须完成 INT／症状阶段；需要回退请读档",
                )
            if action == "check.cancel":
                check = await session.get(CheckRecord, target)
                require(
                    check
                    and check.room_id == room.id
                    and check.cycle_id == cycle.id
                    and check.status == "pending",
                    "没有此待处理检定",
                )
            await self.cancel_cycle(session, room, cycle)
        elif action == "retry":
            require(room.status == "running", "请先恢复游戏")
            require(cycle and cycle.status == "failed", "没有可以重试的失败回合")
            await self.ensure_config(session, room)
            require(
                cycle.state["call_count"] < self.settings.agent_max_calls,
                "本轮调用预算已耗尽，请取消后提交新行动",
            )
            cycle.status = "running"
            cycle.state = {**cycle.state, "status": "running", "safe_error": None}
            check_id = cycle.state.get("pending_check_id")
            check = await session.get(CheckRecord, check_id) if check_id else None
            require(not check or check.status != "cancelled", "检定已取消，请取消此回合后重新行动")
            if (
                check
                and check.status == "pending"
                and cycle.state["current_node"] == "wait_for_human_roll"
            ):
                cycle.status = "waiting_for_roll"
                cycle.state = {**cycle.state, "status": "waiting_for_roll"}
            self.cycle_event(session, room, cycle)
        else:
            require(False, "未知 Agent 操作", 422)

    def cycle_event(self, session, room, cycle):
        stages = dict(cycle.state.get("stage_states", {}))
        if cycle.status != "running":
            prior = stages.get(cycle.state["current_node"], {})
            stages[cycle.state["current_node"]] = {
                **prior,
                "status": cycle.status,
                "safe_error": cycle.state.get("safe_error"),
            }
        cycle.state = {
            **cycle.state,
            "stage_states": {
                name: s.CycleStage.model_validate(value).model_dump(mode="json")
                for name, value in stages.items()
            },
        }
        self.rooms.append(
            session,
            room,
            "agent.cycle_changed",
            room.host_member_id,
            {
                "cycle_id": cycle.id,
                "status": cycle.status,
                "current_node": cycle.state["current_node"],
                "safe_error": cycle.state.get("safe_error"),
                "call_count": cycle.state.get("call_count", 0),
                "wait_reason": cycle.state.get("wait_reason"),
            },
        )

    async def cancel_cycle(self, session, room, cycle, *, restoring=False):
        if cycle.state.get("combat_action_id") and not restoring:
            from app.rooms.combat_service import load_state, store_state

            data = load_state(room)
            action = data.combat.actions.get(cycle.state["combat_action_id"])
            if action and action["stage"] not in {"done", "cancelled"}:
                require(
                    not action["rolls"] and not action.get("damage"),
                    "战斗已有固定骰或伤害，请完成当前结算后取消",
                )
                action["stage"] = "cancelled"
                data.combat.pending_id = None
                store_state(room, data)
        from app.persistence.preparation_models import HostReviewRequest

        review = await session.scalar(
            select(HostReviewRequest).where(
                HostReviewRequest.cycle_id == cycle.id, HostReviewRequest.status == "pending"
            )
        )
        if review:
            review.status, review.resolved_at = "cancelled", utc_now()
        checks = await session.scalars(
            select(CheckRecord).where(
                CheckRecord.cycle_id == cycle.id, CheckRecord.status == "pending"
            )
        )
        for check in checks:
            check.status = "cancelled"
            check.document = {
                **check.document,
                "status": "cancelled",
                "resolved_at": utc_now().isoformat(),
            }
            self.rooms.append(
                session,
                room,
                "check.cancelled",
                check.target_member_id,
                {"check_id": check.id},
                check.document["visibility"],
            )
        cycle.status, cycle.finished_at = "cancelled", utc_now()
        cycle.state = {**cycle.state, "status": "cancelled", "wait_reason": None}
        for binding in await self.bindings(session, room.id):
            binding.status = "idle"
        self.cycle_event(session, room, cycle)

    async def request_check(self, session, room, run, args):
        cycle = await session.get(AgentCycle, run.cycle_id)
        from app.agents.adjudication_schemas import AdjudicationRecord
        from app.agents.check_policy import CheckPolicyEvaluator
        from app.persistence.adjudication_models import ActionPlanRecord
        from app.rules.display import resolve_check_name

        action_record = await session.get(ActionPlanRecord, cycle.id)
        require(action_record is not None, "检定缺少服务端行动计划")
        doc = AdjudicationRecord.model_validate(action_record.document)
        facts = await self.adjudication.facts(session, room, cycle, run)
        from app.agents.check_policy import CheckProposal

        proposal = doc.plan.proposed_check or CheckProposal(**args.model_dump())
        require(
            all(getattr(proposal, k) == getattr(args, k) for k in s.CheckRequest.model_fields),
            "检定参数与已裁决提案不一致",
        )
        policy = CheckPolicyEvaluator().evaluate(proposal, doc.plan.parsed_intent, facts)
        require(policy.allowed, policy.reason)
        require(
            run.graph_node in {"keeper_decide", "keeper_decide_repair", "plan_keeper_action"},
            "每轮仅允许 KP 首次决策请求一次检定",
            422,
        )
        require(cycle.state.get("pending_check_id") is None, "本轮已请求过检定")
        require(
            not cycle.state.get("pending_review_id") or cycle.state.get("review_result"),
            "主机审阅通过后才能创建检定",
        )
        target = await session.get(RoomMember, str(args.target_member_id))
        require(
            target and target.room_id == room.id and target.active and target.role == "player",
            "检定目标不存在",
            404,
        )
        slot = next(
            (slot for slot in await self.rooms.slots(session, room) if slot.member_id == target.id),
            None,
        )
        require(slot is not None, "目标尚未绑定角色")
        try:
            from app.rooms.sanity_service import current_check_value

            value = current_check_value(room, slot, args.kind, args.name)
        except ValueError as error:
            require(False, str(error), 422)
        module = await self.module(session, room.id)
        prepared = await self.entities.binding(session, room.id)
        if args.clue_id and prepared:
            entity = await self.entities.entity(session, room.id, args.clue_id)
            await self.entities.check_conditions(
                session, room, entity, run.cycle_id, for_check=True
            )
            expected = entity.snapshot["reveal_conditions"]["successful_check"]
            require(
                expected
                and (
                    preserves_ordinary_condition(args)
                    and (args.kind, args.name, args.difficulty)
                    == (expected["kind"], expected["name"], expected["difficulty"])
                    or bool(proposal.alternative_basis.strip())
                ),
                "检定与实体批准条件不匹配",
                422,
            )
        elif args.clue_id:
            clue = next(
                (c for c in Module.model_validate(module.document).clues if c.id == args.clue_id),
                None,
            )
            require(
                clue and clue.visibility != "keeper_only", "不能对不存在或私密线索请求公开检定", 422
            )
            pre = clue.prerequisites
            expected = pre.successful_check
            require(
                pre.scene_id == module.state["scene_id"]
                and set(pre.clue_ids) <= set(module.state["revealed_clues"]),
                "检定线索前置条件未满足",
            )
            require(
                expected
                and (
                    preserves_ordinary_condition(args)
                    and (args.kind, args.name, args.difficulty)
                    == (expected.kind, expected.name, expected.difficulty)
                    or bool(proposal.alternative_basis.strip())
                ),
                "检定与线索要求不匹配",
                422,
            )
        check = s.PendingCheck(
            **args.model_dump(),
            display_name=resolve_check_name(
                args.name, args.kind, slot.character_snapshot["ruleset_id"]
            )["display_name"],
            ruleset_id=slot.character_snapshot["ruleset_id"],
            policy_fingerprint=policy.state_fingerprint,
            policy_target_id=policy.target_entity_id,
            attempt_purpose=proposal.purpose,
            attempt_method=proposal.method,
            alternative_basis=proposal.alternative_basis,
            room_id=room.id,
            slot_id=slot.id,
            value=value,
            requester=run.actor_member_id,
            agent_run_id=run.id,
        )
        if check.opposed or check.combined:
            await self.compound.freeze(session, room, check, facts)
        # The private KP model must not publish arbitrary text through a check reason.
        check.reason = f"过一次{check.display_name}，看看能否完成这次尝试：{facts.raw_text[:100]}"
        if check.opposed:
            left, right = check.compound["participants"]
            check.reason = (
                f"双方目标互斥，进行非战斗对抗：{left['label']}的{left['display_name']}"
                f"对{right['label']}的{right['display_name']}。双方原骰完成后依此顺序选择幸运，"
                "选择结束才裁决；完全平局为僵局，不能孤注。"
            )
        elif check.combined:
            first, second = check.compound["components"]
            requirement = "任一成功" if check.combined.requirement == "any" else "全部成功"
            check.reason = (
                f"这次尝试涉及{first['display_name']}与{second['display_name']}，"
                f"共用一次百分骰，条件为{requirement}。"
            )
        record = CheckRecord(
            id=str(check.id),
            room_id=room.id,
            cycle_id=cycle.id,
            target_member_id=target.id,
            agent_run_id=run.id,
            status="pending",
            document=check.model_dump(mode="json"),
        )
        session.add(record)
        cycle.state = {**cycle.state, "pending_check_id": record.id}
        self.rooms.append(
            session,
            room,
            "keeper.narration",
            target.id,
            {
                "text": check.reason,
                "cycle_id": cycle.id,
                "check_invitation": True,
            },
            visibility=check.visibility,
            request_id=cycle.id + ":invitation",
        )
        self.rooms.append(
            session,
            room,
            "check.requested",
            target.id,
            {**self.check_public(record.document), "cycle_id": cycle.id},
            args.visibility,
        )
        return record

    async def resolve_check(self, session, room, record, automatic):
        if record.document.get("opposed"):
            await self.compound.advance(session, room, record)
            return
        if record.document.get("sanity"):
            await self.sanity.roll(
                session, room, record, record.document["sanity"]["stage"], automatic
            )
            return
        require(record.status == "pending", "此检定已解决或取消")
        if record.document.get("settlement"):
            return  # repeated original-roll click never rolls a push or accepts a choice
        member = await session.get(RoomMember, record.target_member_id)
        require(
            member and member.active and (member.controller_type == "agent") == automatic,
            "检定操作来源不匹配",
            403,
        )
        check = s.PendingCheck.model_validate(record.document)
        slot = next(
            (
                slot
                for slot in await self.rooms.slots(session, room)
                if slot.id == str(check.slot_id)
            ),
            None,
        )
        require(slot and slot.member_id == member.id, "角色分配已变化，请取消回合")
        # Snapshot plus confirmed mythos growth; model output never sets values.
        from app.rooms.sanity_service import current_check_value

        if not check.combined:
            check.value = current_check_value(room, slot, check.kind, check.name)
        await self.settlement.rolled(session, room, record, check, automatic)

    async def save(self, session, room, snapshot):
        await self.navigation.save(session, room, snapshot)
        await self.knowledge.save(session, room, snapshot)
        await self.entities.save(session, room, snapshot)
        module = await self.module(session, room.id)
        if module is None:
            return
        cycle = await self.cycle(session, room.id, active=True)
        require(not cycle or cycle.status != "running", "模型正在运行，请等待检定或回合结束再存档")

        def row(record):
            return {
                c.name: (
                    getattr(record, c.name).isoformat()
                    if c.name.endswith("_at") and getattr(record, c.name)
                    else getattr(record, c.name)
                )
                for c in record.__table__.columns
            }

        doc = {
            "module": row(module),
            "bindings": [row(b) for b in await self.bindings(session, room.id)],
            "cycle": row(cycle) if cycle else None,
            "conversation_cycles": [
                row(c)
                for c in await session.scalars(
                    select(AgentCycle).where(
                        AgentCycle.room_id == room.id,
                        AgentCycle.status.in_(["queued", "queued_action", "suspended"]),
                    )
                )
            ],
            "checks": [
                row(c)
                for c in await session.scalars(
                    select(CheckRecord).where(CheckRecord.room_id == room.id)
                )
            ],
            "memories": [row(m) for m in await memories(session, room.id, host=True)],
            "checkpoint_thread_id": cycle.id if cycle else None,
        }
        doc["profiles"] = {
            binding["profile_id"]: self.profile_config(
                (await session.get(ProfileRecord, binding["profile_id"])).document
            )
            for binding in doc["bindings"]
        }
        from app.agents.adjudication_schemas import AdjudicationSaveState
        from app.persistence.adjudication_models import AgentBehaviorRecord, SummaryRecoveryRecord

        doc["adjudication"] = AdjudicationSaveState(
            behaviors={
                r.member_id: r.document
                for r in await session.scalars(
                    select(AgentBehaviorRecord).where(AgentBehaviorRecord.room_id == room.id)
                )
            },
            summary_recoveries={
                r.profile_id: r.document
                for r in await session.scalars(
                    select(SummaryRecoveryRecord).where(SummaryRecoveryRecord.room_id == room.id)
                )
            },
        ).model_dump(mode="json")
        session.add(AgentSaveState(snapshot_id=snapshot.id, document=doc))

    @staticmethod
    def profile_config(document):
        return {key: document[key] for key in s.ProfileInput.model_fields}

    async def load(self, session, room, snapshot):
        await self.knowledge.load(session, room, snapshot)
        saved = await session.get(AgentSaveState, snapshot.id)
        current = await self.cycle(session, room.id, active=True)
        require(not current or current.status != "running", "正在执行的回合不能读档")
        saved_cycle_id = (
            saved.document["cycle"]["id"] if saved and saved.document["cycle"] else None
        )
        if current and current.id != saved_cycle_id:
            await self.cancel_cycle(session, room, current, restoring=True)
            await session.flush()
        if not saved:
            module = await self.module(session, room.id)
            require(module is None, "该旧存档没有模组状态，请使用绑定模组之后的存档")
            return
        data = saved.document
        conversation_cycles = {c["id"]: c for c in data.get("conversation_cycles", [])}
        for row in await session.scalars(
            select(AgentCycle).where(
                AgentCycle.room_id == room.id,
                AgentCycle.status.in_(["queued", "queued_action", "suspended"]),
            )
        ):
            if row.id not in conversation_cycles:
                row.status = "cancelled"
        for cid, document in conversation_cycles.items():
            row = await session.get(AgentCycle, cid)
            row.status, row.state = document["status"], document["state"]
        # Old ordinary saves have no settlement key. They still restore their
        # pending stage; fixed dice remain available from append-only events.
        saved_checks = {c["id"]: c for c in data["checks"]}
        for check in await session.scalars(
            select(CheckRecord).where(CheckRecord.room_id == room.id)
        ):
            if check.id in saved_checks:
                row = saved_checks[check.id]
                check.status, check.document, check.cycle_id = (
                    row["status"],
                    row["document"],
                    row["cycle_id"],
                )
            else:
                check.status = "cancelled"
                check.document = {
                    **check.document,
                    "status": "cancelled",
                    "sanity_rewound": True,
                    "settlement_rewound": True,
                }
        await session.flush()
        from app.agents.adjudication_schemas import AdjudicationSaveState
        from app.persistence.adjudication_models import AgentBehaviorRecord, SummaryRecoveryRecord

        adjudication = AdjudicationSaveState.model_validate(data.get("adjudication", {}))
        for model, key_name, values in (
            (AgentBehaviorRecord, "member_id", adjudication.behaviors),
            (SummaryRecoveryRecord, "profile_id", adjudication.summary_recoveries),
        ):
            for row in await session.scalars(select(model).where(model.room_id == room.id)):
                if getattr(row, key_name) not in values:
                    await session.delete(row)
            for key, value in values.items():
                row = await session.get(model, (room.id, key))
                if row:
                    row.document = value.model_dump(mode="json")
                else:
                    session.add(
                        model(
                            room_id=room.id,
                            **{key_name: key},
                            document=value.model_dump(mode="json"),
                        )
                    )
        for profile_id, document in data.get("profiles", {}).items():
            profile = await session.get(ProfileRecord, profile_id)
            require(
                profile and self.profile_config(profile.document) == document,
                "存档引用的 Agent 档案已修改，请恢复原档案配置后读档",
            )
        module = await self.module(session, room.id)
        module.document, module.content_hash, module.state = (
            data["module"]["document"],
            data["module"]["content_hash"],
            data["module"]["state"],
        )
        module.enabled = data["module"]["enabled"]
        for binding in await self.bindings(session, room.id):
            binding.enabled = False
        await session.flush()
        for saved_binding in data["bindings"]:
            binding = await session.get(RoomAgentBinding, saved_binding["id"])
            member = await session.get(RoomMember, binding.member_id)
            binding.enabled = bool(member and member.active)
            binding.last_consumed_event_seq = saved_binding["last_consumed_event_seq"]
            binding.status = "idle" if binding.enabled else "disabled"
        active_ids = {m["id"] for m in data["memories"]}
        for memory in await session.scalars(
            select(AgentMemory).where(AgentMemory.room_id == room.id)
        ):
            memory.active = memory.id in active_ids
        # Preserve terminal dice and graph progress; pending saved interrupts can still be rolled.
        if data["cycle"]:
            restored = await session.get(AgentCycle, data["cycle"]["id"])
            saved_pending = saved_checks.get(data["cycle"]["state"].get("pending_check_id"))
            if data["cycle"]["state"].get("combat_flow"):
                restored.status = "waiting_for_roll"
                restored.state = {
                    **data["cycle"]["state"],
                    "status": "waiting_for_roll",
                    "safe_error": None,
                }
                self.cycle_event(session, room, restored)
            elif saved_pending and (
                saved_pending["status"] == "pending"
                or data["cycle"]["status"] == "waiting_for_roll"
                and saved_pending["status"] == "resolved"
            ):
                # A decision can commit just before the graph is scheduled.
                # Saving that paid/result-final window must still resume the
                # remaining reveal/SAN/narration stages after a later load.
                restored.status = "waiting_for_roll"
                restored.state = {
                    **data["cycle"]["state"],
                    "status": "waiting_for_roll",
                    "sanity_restore": True,
                    "wait_reason": "sanity_symptom"
                    if (saved_pending["document"].get("sanity") or {}).get("stage") == "symptom"
                    else "opposed_choice"
                    if (saved_pending["document"].get("compound") or {}).get("stage") == "choice"
                    else "opposed_roll"
                    if saved_pending["document"].get("opposed")
                    else {
                        "choice": "check_choice",
                        "push_review": "push_review",
                        "push_roll": "push_roll",
                        "consequence": "push_consequence",
                    }.get(
                        (saved_pending["document"].get("settlement") or {}).get("stage"),
                        "human_roll",
                    ),
                    "safe_error": None,
                }
                self.cycle_event(session, room, restored)
            elif data["cycle"]["state"].get("wait_reason") == "sanity_encounter_review":
                restored.status = "waiting_for_roll"
                restored.state = {
                    **data["cycle"]["state"],
                    "sanity_restore": True,
                    "status": "waiting_for_roll",
                    "safe_error": None,
                }
                self.cycle_event(session, room, restored)
            elif restored.status not in {"completed"}:
                restored.status = "failed"
                restored.state = {
                    **restored.state,
                    "status": "failed",
                    "safe_error": "已载入存档；请恢复房间后重试或取消此回合",
                }
                pending_id = restored.state.get("pending_check_id")
                pending = await session.get(CheckRecord, pending_id) if pending_id else None
                if (
                    pending
                    and pending.status == "pending"
                    and restored.state["current_node"] == "wait_for_human_roll"
                ):
                    restored.status = "waiting_for_roll"
                    restored.state = {
                        **restored.state,
                        "status": "waiting_for_roll",
                        "safe_error": None,
                    }
                self.cycle_event(session, room, restored)
        await self.entities.load(session, room, snapshot)
        await self.navigation.load(session, room, snapshot)
