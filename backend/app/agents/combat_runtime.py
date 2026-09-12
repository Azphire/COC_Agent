"""Compact combat decisions using the existing model, run ledger and serial cycle queue."""

import json
from uuid import uuid4

from app.domain.character import utc_now
from app.persistence.agent_models import AgentCycle, AgentRun, ProfileRecord
from app.persistence.room_models import RoomEvent
from app.rooms.combat_schemas import CombatDecision, CombatNarration
from app.rooms.combat_service import current_actor, load_state, store_state
from app.rooms.service import Identity, RoomError, require

DECISION = (
    "你是CoC跑团KP，只决定这句玩家原话的实际动作。返回CombatDecision。"
    "attack是真实攻击意图；询问、假设、谈话用talk，调查用investigate。"
    "目标和武器ID只复制候选，不能创造数值或替真人选择。近战选已有近战武器，普通单发射击选已有枪械。"
    "攻击目标只能选attack_targets，不能攻击已经脱离本次战斗的角色；追逐属于后续场景裁定。"
    "range_band按场景距离选base/long/extreme/point_blank，不明确距离用base。"
    "first_aid急救；medicine医学；reload装填；pass主动等待或站起等占用行动；end仅表示自己脱离战斗。"
    "急救和医学的目标选treatment_targets；不要将原话中的NPC替换成队友。没有对应目标时用talk说明。"
    "不因聊天自动结束战斗。自动角色结合自己的队伍、伤势和武器选行动，攻击对方，受伤严重可以脱离。"
    "无需主机审批普通合法行动。reason简短描述尝试，不宣布伤害或成败。"
)


async def call_model(runtime, state, schema, instruction, context, node):
    service = runtime.service

    async def prepare(session, room):
        cycle = await session.get(AgentCycle, state["cycle_id"])
        require(room.status == "running" and cycle.status == "running", "请恢复游戏")
        binding = next(
            b
            for b in await service.bindings(session, room.id)
            if b.member_id == room.host_member_id
        )
        profile = await session.get(ProfileRecord, binding.profile_id)
        run = AgentRun(
            id=str(uuid4()),
            room_id=room.id,
            cycle_id=cycle.id,
            profile_id=profile.id,
            actor_member_id=room.host_member_id,
            graph_node=node,
            status="running",
            input_seq_start=state["triggering_event_seq"],
            input_seq_end=room.revision,
            provider=service.settings.model_provider,
            model=service.settings.model_name,
            context=await service.sanitize(session, room, context),
            tool_results=[],
        )
        session.add(run)
        return run.id, run.context

    run_id, safe = await service.mutate(state["room_id"], prepare)

    async def consume():
        async def update(session, room):
            cycle = await session.get(AgentCycle, state["cycle_id"])
            require(room.status == "running" and cycle.status == "running", "回合已停止")
            require(
                cycle.state["call_count"] < service.settings.agent_max_calls, "本轮模型调用达到上限"
            )
            cycle.state = {**cycle.state, "call_count": cycle.state["call_count"] + 1}

        await service.mutate(state["room_id"], update)

    result, latency = await service.model.generate(
        [
            {"role": "system", "content": instruction},
            {"role": "user", "content": json.dumps(safe, ensure_ascii=False)},
        ],
        response_schema=schema,
        max_attempts=2,
        on_call=consume,
        on_result=runtime.call_recorder(state["room_id"], run_id),
        output_limit=900,
    )

    async def finish(session, room):
        run = await session.get(AgentRun, run_id)
        run.structured_output = result.structured.model_dump(mode="json")
        run.status, run.finished_at, run.latency_ms = "completed", utc_now(), latency
        return run.structured_output

    return schema.model_validate(await service.mutate(state["room_id"], finish))


async def reveal_addressed_npc(service, session, room, raw_text, scene, cycle_id=None):
    """Bridge an explicitly addressed, already described NPC into ordinary treatment."""
    summary = next(
        (
            s.get("public_description", "")
            for s in scene.document.get("scenes", [])
            if s["id"] == scene.state["scene_id"]
        ),
        "",
    )
    for entity in await service.entities.rows(session, room.id):
        title = entity.snapshot.get("title", "")
        if (
            entity.entity_type != "npc"
            or entity.state != "hidden"
            or not title
            or title not in raw_text
            or title not in summary
        ):
            continue
        try:
            await service.entities.check_conditions(session, room, entity, cycle_id)
        except RoomError:
            continue  # A named but gated NPC still requires its actual source condition.
        await service.entities.reveal(
            session, room, entity.source_entity_id, room.host_member_id, cycle_id=cycle_id
        )


async def drive_combat(runtime, state):
    service, rooms = runtime.service, runtime.rooms
    combat_service = service.combat

    async def prepare(session, room):
        cycle = await session.get(AgentCycle, state["cycle_id"])
        data = load_state(room)
        module = await service.module(session, room.id)
        await combat_service.ensure_members(session, room, data, module.state["scene_id"])
        store_state(room, data)
        trigger = await session.get(RoomEvent, (room.id, state["triggering_event_seq"]))
        await reveal_addressed_npc(
            service, session, room, trigger.payload["text"], module, cycle.id
        )
        data = load_state(room)
        actor = state.get("combat_actor_id") or state["triggering_member_id"]
        public = await combat_service.view(session, room, Identity(actor, False))
        own = data.combat.participants.get(actor)
        from app.preparation.encounters import can_locate

        recent = [
            {"actor_id": e["actor_member_id"], "text": e["payload"]["text"]}
            for e in await rooms.events(session, room, Identity(actor, False))
            if e["visibility"] == "public"
            and e["type"] in {"action.submitted", "chat.message"}
            and e["payload"].get("text")
        ][-6:]
        context = {
            "input": trigger.payload["text"],
            "actor_id": actor,
            "automatic": bool(state.get("combat_automatic")),
            "recent_player_actions": recent,
            "combat": public,
            "attack_targets": [
                pid
                for pid in (data.combat.order if data.combat.active else data.combat.participants)
                if pid != actor
                and data.combat.participants[pid].scene_id == module.state["scene_id"]
                and data.combat.participants[pid].public
                and (not own or can_locate(data, own, data.combat.participants[pid]))
                and not public["participants"].get(pid, {}).get("incapacitated")
            ],
            "treatment_targets": [
                {"id": p.id, "label": p.label}
                for p in data.combat.participants.values()
                if p.public
                and p.scene_id == module.state["scene_id"]
                and not p.injury.dead
                and (p.hp < p.hp_max or p.injury.dying)
            ],
            "scene": next(
                (
                    {k: s.get(k) for k in ("title", "public_description")}
                    for s in module.document.get("scenes", [])
                    if s["id"] == module.state["scene_id"]
                ),
                {k: room.session_state.get(k) for k in ("scene_title", "scene_summary")},
            ),
            "own": own.model_dump(exclude={"source", "injury"}) if own else None,
        }
        # Own receipt history and other people's hidden numbers do not enter the planner.
        if cycle.state.get("combat_action_id"):
            await combat_service.advance(session, room, cycle)
            action = load_state(room).combat.actions[cycle.state["combat_action_id"]]
            if action["stage"] == "done":
                cycle.status = "running"
                cycle.state = {**cycle.state, "status": "running", "wait_reason": None}
        return cycle.state, context

    state, context = await service.mutate(state["room_id"], prepare)
    if state.get("combat_return") and state["status"] == "running":

        async def return_to_graph(session, room):
            cycle = await session.get(AgentCycle, state["cycle_id"])
            cycle.state = {**cycle.state, "combat_flow": False, "combat_return": False}
            if cycle.state.get("request_category") == "san_encounter":
                cycle.status, cycle.finished_at = "completed", utc_now()
                cycle.state = {**cycle.state, "status": "completed", "wait_reason": None}
                service.cycle_event(session, room, cycle)

        await service.mutate(state["room_id"], return_to_graph)
        return False
    if state.get("combat_action_id") and state["status"] == "waiting_for_roll":
        return True
    if not state.get("combat_action_id") and not state.get("combat_started_only"):
        decision = (
            CombatDecision.model_validate(state["combat_decision"])
            if state.get("combat_decision")
            else await call_model(
                runtime, state, CombatDecision, DECISION, context, "combat_decide"
            )
        )

        async def decide(session, room):
            cycle = await session.get(AgentCycle, state["cycle_id"])
            cycle.state = {**cycle.state, "combat_decision": decision.model_dump(mode="json")}
            if decision.operation in {"investigate", "talk"} and not state.get("combat_automatic"):
                data = load_state(room)
                require(
                    decision.operation == "talk"
                    or not data.combat.active
                    or current_actor(data.combat) == context["actor_id"],
                    "可以随时交谈；消耗行动的调查请等待自己的行动次序",
                )
                cycle.state = {
                    **cycle.state,
                    "combat_flow": False,
                    "combat_other_action": decision.operation == "investigate"
                    and bool(room.session_state.get("combat", {}).get("active")),
                }
                return False
            # Automatic actors may choose a tactical pause; they never take a human turn.
            actual = (
                decision.model_copy(update={"operation": "pass"})
                if decision.operation in {"talk", "investigate"}
                else decision
            )
            try:
                # The nested transaction prevents failed validation from partially starting combat.
                async with session.begin_nested():
                    await combat_service.create_action(
                        session, room, cycle, context["actor_id"], actual
                    )
            except RoomError as error:
                # Preserve a reviewable rule/permission rejection, without inventing a result.
                await session.refresh(room)
                await session.refresh(cycle)
                cycle.state = {**cycle.state, "combat_rejection": error.message}
            return True

        if not await service.mutate(state["room_id"], decide):
            return False
        state, context = await service.mutate(state["room_id"], prepare)
    async with rooms.database.sessions() as session:
        cycle = await session.get(AgentCycle, state["cycle_id"])
        state = cycle.state
        if cycle.status == "waiting_for_roll":
            return True
        room = await rooms.room(session, state["room_id"])
        data = load_state(room)
        action = data.combat.actions.get(state.get("combat_action_id"))
        receipt = combat_service.public_action(data, action, Identity("public", False))
        public = await combat_service.view(session, room, Identity("public", False))
        narrative_context = {
            "input": context["input"],
            "result": receipt,
            "rejection": state.get("combat_rejection"),
            "started": state.get("combat_started_only", False),
            "current_actor": public["participants"]
            .get(public["current_actor_id"], {})
            .get("label"),
            "scene": context["scene"],
        }
    output = (
        CombatNarration(
            text=(
                state.get("combat_rejection")
                or (
                    f"冲突开始，按行动顺序由{narrative_context['current_actor']}先行动。"
                    "发起者的攻击尚未掷骰，请等待自己的行动次序。"
                )
            )
        )
        if action is None
        else await call_model(
            runtime,
            state,
            CombatNarration,
            "你是中文跑团KP。按服务端result简短解释发生的攻防和可见后果，再交回当前行动者。"
            "只描述已结算结果；rejection是尚未执行的原因，started只表示开始战斗。"
            "不得补写数值、死亡、额外攻击或替玩家作决定。隐藏数值不推测。用角色名，不输出内部ID。",
            narrative_context,
            "combat_narration",
        )
    )

    async def publish(session, room):
        cycle = await session.get(AgentCycle, state["cycle_id"])
        if cycle.status == "completed":
            return
        data = load_state(room)
        action = data.combat.actions.get(cycle.state.get("combat_action_id"))
        if action:
            require(action["stage"] == "done", "战斗结算尚未完成")
            rooms.append(
                session,
                room,
                "combat.resolved",
                room.host_member_id,
                combat_service.public_action(data, action, Identity("public", False)),
            )
            rooms.append(session, room, "combat.audit", room.host_member_id, action, "host_only")
            for p in data.combat.participants.values():
                if p.member_id:
                    rooms.append(
                        session,
                        room,
                        "combat.receipt",
                        p.member_id,
                        combat_service.public_action(data, action, Identity(p.member_id, False)),
                        "actor_and_host",
                    )
        text = await service.sanitize(session, room, output.text)
        if action and action.get("summary") and action["summary"] not in text:
            text = action["summary"] + "\n" + text
        from app.agents.tools import ensure_public_text

        module = await service.module(session, room.id)
        ensure_public_text(module, text)
        rooms.append(
            session,
            room,
            "keeper.narration",
            room.host_member_id,
            {"text": text, "cycle_id": cycle.id, "combat": True},
        )
        cycle.status, cycle.finished_at = "completed", utc_now()
        cycle.state = {**cycle.state, "status": "completed", "wait_reason": None}
        if (
            cycle.state.get("combat_automatic")
            and cycle.state.get("combat_rejection")
            and not cycle.state.get("combat_started_only")
        ):
            cycle.status = "failed"
            cycle.state = {
                **cycle.state,
                "status": "failed",
                "safe_error": cycle.state["combat_rejection"],
            }
        service.cycle_event(session, room, cycle)
        await session.flush()
        from app.agents.conversation import activate_next

        await activate_next(service, session, room)
        await combat_service.queue_automatic(session, room)

    await service.mutate(state["room_id"], publish)
    runtime.schedule(state["room_id"])
    return True
