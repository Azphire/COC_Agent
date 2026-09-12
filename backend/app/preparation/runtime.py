"""Source-approved inventory and event effects, never parsed from public narration."""

import re

from sqlalchemy import select

from app.persistence.agent_models import AgentCycle, CheckRecord
from app.persistence.room_models import RoomEvent
from app.preparation.runtime_schemas import ModuleInteraction
from app.rooms.combat_service import load_state, store_state
from app.rooms.service import require


async def apply_interaction(agents, session, room, args, *, run=None, host=False):
    entity = await agents.entities.entity(session, room.id, args.entity_id)
    definition = next(
        (r for r in entity.snapshot.get("interactions", []) if r["id"] == args.interaction_id),
        None,
    )
    require(definition, "没有此批准交互", 422)
    rule = ModuleInteraction.model_validate(definition)
    cycle = await session.get(AgentCycle, run.cycle_id) if run else None
    seq = cycle.state["triggering_event_seq"] if cycle else args.source_event_seq
    event = await session.get(RoomEvent, (room.id, seq))
    require(
        event and event.type in {"action.submitted", "agent.action_proposed"},
        "交互必须引用本房间实际玩家行动",
        422,
    )
    require(args.evidence_quote in event.payload.get("text", ""), "交互引用并非玩家原话", 422)
    actor = event.actor_member_id
    state = load_state(room)
    key = f"{seq}:{entity.source_entity_id}:{rule.id}"
    if key in state.module_runtime.receipts:
        return state.module_runtime.receipts[key]
    require(not state.module_runtime.outcome, "模组已经结束")
    require(
        not state.module_runtime.pending_outcome
        or rule.outcome == state.module_runtime.pending_outcome,
        "请先结算当前终幕遭遇",
    )
    require(not state.combat.pending_id, "请先完成攻击或伤势结算")
    if rule.outcome or rule.prepare_outcome:
        agents.combat.require_movement_ready(room)
    nav = await agents.navigation.require_available(session, room.id)
    snapshot, _ = await agents.navigation.snapshot(session, nav)
    last_move = await session.scalar(
        select(RoomEvent.seq)
        .where(RoomEvent.room_id == room.id, RoomEvent.type == "scene.updated")
        .order_by(RoomEvent.seq.desc())
        .limit(1)
    )
    require(not last_move or seq > last_move, "不能用转场前的行动确认当前场景交互")
    require(
        any(
            b.entity_id == args.entity_id and b.node_id == nav.current_scene_node_id
            for b in snapshot.entity_bindings
        )
        or args.entity_id in state.module_runtime.inventory,
        "交互实体不在当前场景",
    )
    require(entity.state != "hidden", "交互目标尚未实际揭示")
    if not host:
        from app.agents.adjudication_schemas import AdjudicationRecord
        from app.persistence.adjudication_models import ActionPlanRecord

        record = await session.get(ActionPlanRecord, cycle.id)
        require(record, "交互缺少玩家行动计划")
        doc = AdjudicationRecord.model_validate(record.document)
        facts = await agents.adjudication.facts(session, room, cycle, run)
        intent = doc.plan.parsed_intent
        require(
            agents.adjudication.validator.validate_intent(intent, doc.plan, facts) is None,
            "交互行动未通过验证",
        )
        require(
            intent.type in {"interact", "use_item", "investigate"}
            and intent.target_id == args.entity_id,
            "交互目标不匹配玩家实际行动",
        )
        actual_action = doc.plan.focus.action if doc.plan.focus else ""
        require(
            actual_action
            and actual_action in event.payload.get("text", "")
            and re.search(
                r"拿|取|拾|收起|保管|打开|开启|关闭|插入|使用|解锁|推动|推下|拉动|"
                r"扔|投|抛|潜行|绕过|穿过|扶|背起|带上|携带|告知|告诉|关掉|加速|减速|停车|"
                r"\b(?:take|pick|use|unlock|open|close|throw|push|pull|carry|sneak)\b",
                actual_action,
                re.IGNORECASE,
            )
            and not re.search(r"是否|能否|可否|假如|如果|假设|不要|并未|没有|[?？]", actual_action),
            "只有观察或询问不能执行物品和事件操作，请主机核对实际行动",
        )
    require(host or not rule.host_review, "此原文特殊方法需要KP确认实际情境")
    require(
        all(state.module_runtime.inventory.get(eid) == actor for eid in rule.required_item_ids),
        "所需物品必须由本次实际行动者持有，不能使用队友的物品",
    )
    require(
        all(state.module_runtime.flags.get(k, False) == v for k, v in rule.required_flags.items()),
        "交互事件条件未满足",
    )
    require(
        set(rule.required_item_ids) <= set(state.module_runtime.inventory), "尚未实际持有所需物品"
    )
    visible = {
        e.source_entity_id
        for e in await agents.entities.rows(session, room.id)
        if e.state != "hidden"
    }
    require(set(rule.required_entity_ids) <= visible, "所需事实尚未揭示")
    if rule.required_sanity:
        records = list(
            await session.scalars(select(CheckRecord).where(CheckRecord.room_id == room.id))
        )
        slots = [s for s in await agents.rooms.slots(session, room) if s.member_id]
        for required in rule.required_sanity:
            for slot in slots:
                require(
                    any(
                        c.target_member_id == slot.member_id
                        and not c.document.get("sanity_rewound")
                        and (c.document.get("sanity") or {}).get("entity_id") == required.entity_id
                        and (c.document.get("sanity") or {}).get("effect", {}).get("id")
                        == required.effect_id
                        and (c.document.get("sanity") or {}).get("stage") == "done"
                        for c in records
                    ),
                    "终幕须先完成每位调查员的原稿SAN结算",
                )
    if rule.max_npc_count is not None:
        require(
            rule.opposed_npc_id is not None
            and state.module_runtime.npc_counts.get(rule.opposed_npc_id, 0) <= rule.max_npc_count,
            "实际怪物数量不允许此逃脱方法",
        )
    if rule.check_name:
        checks = list(
            await session.scalars(
                select(CheckRecord).where(
                    CheckRecord.room_id == room.id,
                    CheckRecord.target_member_id == actor,
                    CheckRecord.status == "resolved",
                )
            )
        )
        matched = []
        for check in checks:
            check_cycle = await session.get(AgentCycle, check.cycle_id)
            if not check_cycle or check_cycle.state.get("triggering_event_seq") != seq:
                continue
            c = check.document
            if (
                c.get("name") != rule.check_name
                or c.get("result", {}).get("passed") != rule.check_passed
            ):
                continue
            if rule.opposed_npc_id:
                if (c.get("opposed") or {}).get("opponent_npc_id") != rule.opposed_npc_id:
                    continue
                if c.get("result", {}).get("winner_id") != actor:
                    continue
            matched.append(check.id)
        require(matched, "必须先结算本次行动所需的真实检定；主机确认也不能替代骰点")
    else:
        matched = []
    state.module_runtime.flags.update(rule.set_flags)
    for eid in rule.acquire_item_ids:
        item = await agents.entities.entity(session, room.id, eid)
        require(item.entity_type == "item" and item.state != "hidden", "所获物品必须已实际发现")
        state.module_runtime.inventory.setdefault(eid, actor)
    state.module_runtime.following_npc_ids = list(
        dict.fromkeys(
            [
                *state.module_runtime.following_npc_ids,
                *rule.following_npc_ids,
            ]
        )
    )
    state.module_runtime.blocked_scene_node_ids = list(
        dict.fromkeys(
            [
                *state.module_runtime.blocked_scene_node_ids,
                *rule.blocked_scene_node_ids,
            ]
        )
    )
    receipt = {
        "entity_id": args.entity_id,
        "interaction_id": rule.id,
        "source_event_seq": seq,
        "actor_member_id": actor,
        "text": rule.public_result,
        "check_ids": matched,
        "host_confirmed": host,
        "reason": args.reason if host else "批准玩家行动",
        "outcome": rule.outcome,
    }
    if rule.outcome:
        awards = []
        all_survive = all(c.hp and not c.injury.dead for c in state.characters.values())
        for slot in await agents.rooms.slots(session, room):
            from uuid import UUID

            c = state.characters.get(UUID(slot.id))
            if not c or c.san is None:
                continue
            before = c.san
            if rule.san_zero:
                c.sanity.day_loss += c.san
                c.san = 0
                c.sanity.kind, c.sanity.phase = "permanent", "bout"
            for index, reward in enumerate(rule.san_rewards):
                if (
                    not c.hp
                    or c.injury.dead
                    or c.san == 0
                    or reward.all_survive
                    and not all_survive
                    or not all(
                        state.module_runtime.flags.get(k, False) == v
                        for k, v in reward.required_flags.items()
                    )
                ):
                    continue
                roll = await agents.combat.fixed(
                    session, room, {"id": key}, f"reward:{slot.id}:{index}", formula=reward.formula
                )
                c.san = min(c.san_max or 99, c.san + roll["total"])
                awards.append({"slot_id": slot.id, "roll": roll})
            c.sanity.mythos_gain = min(99, c.sanity.mythos_gain + rule.mythos_reward)
            base_mythos = slot.character_snapshot.get("skill_values", {}).get("cthulhu_mythos", 0)
            c.san_max = max(0, 99 - base_mythos - c.sanity.mythos_gain)
            c.san = min(c.san, c.san_max)
            c.sanity.history.append(
                {"source": key, "outcome": rule.outcome, "before": before, "after": c.san}
            )
        receipt["rewards"] = awards
    state.module_runtime.receipts[key] = receipt
    state.module_runtime.outcome = rule.outcome or state.module_runtime.outcome
    state.module_runtime.pending_outcome = None if rule.outcome else rule.prepare_outcome
    store_state(room, state)
    for eid in rule.reveal_entity_ids:
        await agents.entities.reveal(
            session,
            room,
            eid,
            room.host_member_id,
            cycle_id=cycle.id if cycle else None,
            host_override=True,
        )
    agents.rooms.append(
        session,
        room,
        "module.interaction",
        actor,
        {
            "text": rule.public_result,
            "source_event_seq": seq,
            "cycle_id": cycle.id if cycle else None,
        },
    )
    agents.rooms.append(session, room, "module.interaction_receipt", actor, receipt, "host_only")
    if rule.outcome:
        module = await agents.module(session, room.id)
        module.state = {**module.state, "completed": True, "outcome": rule.outcome}
        agents.rooms.append(
            session,
            room,
            "module.completed",
            actor,
            {"text": rule.public_result, "outcome": rule.outcome},
        )
    await agents.navigation.refresh(session, room, nav, snapshot)
    await agents.navigation.persist(session, nav)
    await agents.combat.sync_navigation(session, room, nav, snapshot)
    return receipt


async def freeze_adjustment(agents, session, room, check, entity):
    """No model numeric overrides: select an exact sourced rule using current facts."""
    from app.preparation.runtime_schemas import PreparedCheckAdjustment

    nav = await agents.navigation.state(session, room.id)
    candidates = [
        PreparedCheckAdjustment.model_validate(r)
        for r in (entity or {}).get("check_adjustments", [])
        if (r["kind"], r["name"]) == (check.kind, check.name)
        and (not r.get("opposed_only") or check.opposed)
        and (
            not r.get("scene_node_ids") or nav and nav.current_scene_node_id in r["scene_node_ids"]
        )
    ]
    if not candidates:
        return
    state = load_state(room)
    visible = {
        r.source_entity_id
        for r in await agents.entities.rows(session, room.id)
        if r.state != "hidden"
    }
    candidates = [
        r
        for r in candidates
        if all(state.module_runtime.flags.get(k, False) == v for k, v in r.required_flags.items())
        and set(r.required_entity_ids) <= visible
        and not set(r.forbidden_entity_ids) & visible
    ]
    require(len(candidates) == 1, "原稿数值修正的照明或事件条件尚未唯一确定")
    rule = candidates[0]
    sub = 0
    if rule.subtract_die:
        roll = await agents.combat.fixed(
            session, room, {"id": str(check.id)}, "module_adjustment", formula=rule.subtract_die
        )
        sub = roll["total"]
    base = check.value
    check.value = max(0, min(999, base * rule.numerator // rule.denominator + rule.add - sub))
    check.module_adjustment = {
        "rule": rule.model_dump(),
        "source_entity_id": entity["id"],
        "base_value": base,
        "subtract_roll": sub,
        "value": check.value,
    }


def current_entity_ids(snapshot, node_ids, runtime):
    ids = {b.entity_id for b in snapshot.entity_bindings if b.node_id in node_ids}
    ids |= set(runtime.get("inventory", {}))
    for eid, location in runtime.get("npc_locations", {}).items():
        if location in node_ids:
            ids.add(eid)
        else:
            ids.discard(eid)
    return ids
