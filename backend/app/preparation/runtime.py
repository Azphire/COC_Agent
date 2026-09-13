"""Source-approved inventory and event effects, never parsed from public narration."""

from sqlalchemy import select

from app.persistence.agent_models import AgentCycle, CheckRecord
from app.persistence.room_models import RoomEvent
from app.preparation.runtime_schemas import ModuleInteraction
from app.rooms.combat_service import load_state, store_state
from app.rooms.service import require


async def apply_interaction(agents, session, room, args, *, run=None, host=False):
    from app.preparation.inventory import held_instance

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
    if rule.inventory_operation == "initial" and actor in state.module_runtime.initial_belongings:
        initial = state.module_runtime.initial_belongings[actor]
        return next(
            r
            for r in state.module_runtime.receipts.values()
            if r.get("source_event_seq") == initial["source_event_seq"]
            and r.get("actor_member_id") == actor
        )
    if rule.once_per_actor:
        prior = next(
            (
                r
                for r in state.module_runtime.receipts.values()
                if r.get("entity_id") == args.entity_id
                and r.get("interaction_id") == rule.id
                and r.get("actor_member_id") == actor
            ),
            None,
        )
        if prior:
            return prior
    require(not state.module_runtime.outcome, "模组已经结束")
    if rule.requires_party_loss:
        require(
            bool(state.characters)
            and all(c.injury.dead for c in state.characters.values())
            or state.module_runtime.flags.get("party_consumed")
            or state.module_runtime.flags.get("divine_manifested"),
            "此结局需要实际全员死亡、已结算吞噬或已发生的可选神祇显现，威胁不构成结局",
        )
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
    require(
        not rule.scene_node_ids or nav.current_scene_node_id in rule.scene_node_ids,
        "此交互不适用于当前场景",
    )
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
        or args.entity_id
        in current_entity_ids(
            snapshot, [nav.current_scene_node_id], state.module_runtime.model_dump()
        ),
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
        from app.preparation.adjudication import matches_action_focus

        require(
            intent.type in {"interact", "use_item", "investigate", "observe", "converse"}
            and matches_action_focus(doc.plan, nav.current_scene_node_id, args.entity_id, rule),
            "交互目标不匹配玩家实际行动",
        )
        actual_action = doc.plan.focus.action if doc.plan.focus else ""
        ruling = state.module_runtime.rulings.get(key)
        require(
            actual_action and actual_action in event.payload.get("text", ""), "交互缺少实际行动片段"
        )
        if rule.kp_enabled:
            from app.preparation.action_authority import authority_error, selected_action_matches

            error = authority_error(
                doc.plan.action_authority,
                rule,
                state.module_runtime,
                actor=actor,
                seq=seq,
                scene=nav.current_scene_node_id,
                item_id=args.used_item_id,
                recipient=args.recipient_member_id,
            )
            require(not error, error or "动作不适用")
            require(
                doc.plan.action_authority.get("action") == actual_action,
                "方法选择之后行动片段发生变化",
            )
            require(
                ruling
                and ruling.get("action") == actual_action
                and ruling.get("actor_member_id") == actor,
                "缺少本次有界KP裁定",
            )
            require(
                selected_action_matches(
                    doc.plan.action_authority,
                    ruling.get("selected_action_quote", actual_action),
                    rule,
                ),
                "所选分句不授权此操作",
            )
            verification = ruling.get("current_action_verification") or {}
            require(
                verification.get("matches") and verification.get("action_quote") == actual_action,
                "当前动作未授权此具体操作",
            )
        else:
            # Compatibility for old packages: retain their conservative action guard.
            import re

            require(
                re.search(
                    r"拿|取|拾|打开|使用|解锁|推|拉|扔|投|抛|潜行|绕过|扶|背起|携带|告诉|关|加速|减速|停车|\b(?:take|pick|use|unlock|open|close|throw|push|pull|carry|sneak)\b",
                    actual_action,
                    re.I,
                )
                and not re.search(
                    r"是否|能否|可否|假如|如果|假设|不要|并未|没有|[?？]", actual_action
                ),
                "只有观察或询问不能执行物品和事件操作",
            )
    require(
        host or not rule.host_review or rule.kp_enabled and key in state.module_runtime.rulings,
        "此原文特殊方法需要KP确认实际情境",
    )
    require(
        all(held_instance(state.module_runtime, eid, actor) for eid in rule.required_item_ids),
        "所需物品必须由本次实际行动者持有，不能使用队友的物品",
    )
    require(
        all(state.module_runtime.flags.get(k, False) == v for k, v in rule.required_flags.items()),
        "交互事件条件未满足",
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
            0 < len(state.module_runtime.grapples.get(actor, [])) <= rule.max_npc_count,
            "实际抓握人数不允许此逃脱方法",
        )
    if rule.observation_effect_id:
        require(
            not rule.visibility_any_flags
            or any(state.module_runtime.flags.get(k) for k in rule.visibility_any_flags),
            "尚无足够照明，不能结算实际目睹",
        )
    needs_check = bool(rule.check_name)
    if rule.carry_attribute:
        slot = next(
            (s for s in await agents.rooms.slots(session, room) if s.member_id == actor), None
        )
        value = (
            slot.character_snapshot.get("effective_attributes", {}).get(rule.carry_attribute)
            if slot
            else None
        )
        require(value is not None, "背负伤员需要行动者的力量／体质")
        needs_check = value < 70
    if needs_check:
        checks = list(
            await session.scalars(
                select(CheckRecord).where(
                    CheckRecord.room_id == room.id,
                    CheckRecord.target_member_id == actor,
                    CheckRecord.status == "resolved",
                )
            )
        )
        relevant = []
        for check in checks:
            check_cycle = await session.get(AgentCycle, check.cycle_id)
            if (
                check_cycle
                and check_cycle.state.get("triggering_event_seq") == seq
                and check.document.get("name") == rule.check_name
            ):
                relevant.append(check)
        if (
            relevant
            and not relevant[-1].document.get("result", {}).get("passed")
            and rule.failure_interaction_id
        ):
            failure = next(
                (
                    r
                    for r in entity.snapshot["interactions"]
                    if r["id"] == rule.failure_interaction_id
                ),
                None,
            )
            require(failure and not failure.get("check_passed", True), "缺少批准失败结算")
            rule = ModuleInteraction.model_validate(failure)
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
                if rule.check_passed and c.get("result", {}).get("winner_id") != actor:
                    continue
            matched.append(check.id)
        require(matched, "必须先结算本次行动所需的真实检定；主机确认也不能替代骰点")
    else:
        matched = []
    from app.preparation.encounters import apply_encounter
    from app.preparation.inventory import apply_inventory

    await apply_inventory(
        agents, session, room, state, rule, args, actor, nav.current_scene_node_id, seq, matched
    )
    apply_encounter(state, rule, args, actor, nav.current_scene_node_id, seq)
    state.module_runtime.flags.update(rule.set_flags)
    if rule.encounter_operation in {"sound_start", "sound_stop", "sound_once"}:
        state.module_runtime.flags["continuous_sound"] = any(
            s.get("active") and s.get("continuous") for s in state.module_runtime.sounds.values()
        )
    for eid in rule.acquire_item_ids:
        item = await agents.entities.entity(session, room.id, eid)
        require(item.entity_type == "item" and item.state != "hidden", "所获物品必须已实际发现")
        require(eid not in state.module_runtime.consumed_items, "物品已经消耗，不能重新取得")
        require(eid not in state.module_runtime.dropped_items, "放下的物品须在实际位置拾取")
        holder = state.module_runtime.inventory.get(eid)
        require(holder in {None, actor}, "物品已有持有者，必须由持有者实际交接")
        state.module_runtime.inventory[eid] = actor
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
        "scene_node_id": nav.current_scene_node_id,
        "consequence_entity_ids": rule.reveal_entity_ids
        if rule.prepare_outcome or rule.outcome
        else [],
        "source_event_seq": seq,
        "actor_member_id": actor,
        "text": rule.public_result,
        "check_ids": matched,
        "host_confirmed": host,
        "kp_ruling": state.module_runtime.rulings.get(key),
        "inventory": dict(state.module_runtime.inventory),
        "recipient_member_id": args.recipient_member_id,
        "reason": args.reason if host else "批准玩家行动",
        "outcome": rule.outcome,
    }
    if rule.observation_effect_id:
        observation = {
            "actor_member_id": actor,
            "source_event_seq": seq,
            "cycle_id": cycle.id if cycle else None,
            "scene_node_id": nav.current_scene_node_id,
            "entity_id": rule.observation_entity_id or args.entity_id,
            "effect_id": rule.observation_effect_id,
            "text": rule.public_result,
            "source_block_ids": rule.source_block_ids,
            "origin": "interaction_receipt",
            "established": True,
        }
        state.module_runtime.observations[key] = observation
        receipt["observation"] = observation
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
                if reward.surviving_npc_id and not any(
                    p.npc_id == reward.surviving_npc_id and p.hp and not p.injury.dead
                    for p in state.combat.participants.values()
                ):
                    continue
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
        and not r.get("combat_only")
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
    # Actual terminal consequences belong to the scene where they happened,
    # even when their source paragraphs live in a separate ending section.
    # No pending flag or player assertion can introduce an unplayed ending.
    ids |= {
        eid
        for receipt in runtime.get("receipts", {}).values()
        if receipt.get("scene_node_id") in node_ids
        for eid in receipt.get("consequence_entity_ids", [])
    }
    instances = runtime.get("item_instances", {})
    ids |= {instances.get(key, key) for key in runtime.get("inventory", {})}
    ids |= {
        instances.get(eid, eid)
        for eid, node in runtime.get("dropped_items", {}).items()
        if node in node_ids
    }
    for eid, location in runtime.get("npc_locations", {}).items():
        if location in node_ids:
            ids.add(eid)
        else:
            ids.discard(eid)
    return ids
