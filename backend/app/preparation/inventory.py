"""Authorised transfers of prepared item identities, within the current room."""

from app.rooms.service import require


def held_instance(runtime, entity_id, actor, instance_id=None):
    matches = [
        key
        for key, holder in runtime.inventory.items()
        if holder == actor and runtime.item_instances.get(key, key) == entity_id
    ]
    if instance_id is not None:
        return instance_id if instance_id in matches else None
    return matches[0] if len(matches) == 1 else None


async def public_inventory(agents, session, room):
    runtime = room.session_state.get("module_runtime", {})
    visible = {e["id"]: e for e in await agents.entities.public(session, room.id)}
    members = {m.id: m.display_name for m in await agents.rooms.members(session, room)}
    definitions = {
        e.source_entity_id: e.snapshot for e in await agents.entities.rows(session, room.id)
    }
    return [
        {
            "instance_id": eid,
            "item_id": runtime.get("item_instances", {}).get(eid, eid),
            "title": visible[runtime.get("item_instances", {}).get(eid, eid)]["title"],
            "holder_id": holder,
            "holder_name": members.get(holder, "未知持有者"),
            "remaining_uses": runtime.get("item_uses", {}).get(
                eid,
                definitions.get(runtime.get("item_instances", {}).get(eid, eid), {}).get(
                    "item_uses"
                ),
            ),
        }
        for eid, holder in runtime.get("inventory", {}).items()
        if runtime.get("item_instances", {}).get(eid, eid) in visible
    ]


async def apply_inventory(agents, session, room, state, rule, args, actor, node, seq, checks):
    runtime, op = state.module_runtime, rule.inventory_operation
    if not op:
        return
    eid = rule.item_id or args.entity_id
    item = await agents.entities.entity(session, room.id, eid)
    require(
        item.entity_type == "item" and (item.state != "hidden" or op in {"initial", "recover"}),
        "物品尚未实际发现",
    )
    instance = held_instance(runtime, eid, actor, args.item_instance_id)
    if op in {"give", "drop", "consume"}:
        require(instance, "交出、放下或消耗只能来自本次行动者持有物")
    if op == "give":
        recipients = {s.member_id for s in await agents.rooms.slots(session, room) if s.member_id}
        members = {m.id for m in await agents.rooms.members(session, room) if m.active}
        require(
            args.recipient_member_id in recipients & members and args.recipient_member_id != actor,
            "接收人必须是本房间当前调查员",
        )
        runtime.inventory[instance] = args.recipient_member_id
        if instance in runtime.sounds:
            runtime.sounds[instance]["actor_id"] = args.recipient_member_id
    elif op == "drop":
        runtime.inventory.pop(instance)
        runtime.dropped_items[instance] = node
        if instance in runtime.sounds:
            runtime.sounds[instance]["actor_id"] = None
            runtime.sounds[instance]["scene_node_id"] = node
    elif op == "pickup":
        dropped = [
            key
            for key, location in runtime.dropped_items.items()
            if location == node
            and runtime.item_instances.get(key, key) == eid
            and (not args.item_instance_id or key == args.item_instance_id)
        ]
        require(
            len(dropped) == 1,
            "物品未放在当前场景或已有持有者",
        )
        runtime.dropped_items.pop(dropped[0])
        runtime.inventory[dropped[0]] = actor
        if dropped[0] in runtime.sounds:
            runtime.sounds[dropped[0]]["actor_id"] = actor
    elif op == "consume":
        require(rule.consume_amount == 1, "此独立物品只有一件可消耗")
        runtime.inventory.pop(instance)
        runtime.consumed_items[instance] = runtime.consumed_items.get(instance, 0) + 1
        if instance in runtime.sounds:
            runtime.sounds[instance]["active"] = False
            from app.preparation.encounters import end_transient_sound

            end_transient_sound(state, node)
    elif op == "recover":
        initial = runtime.initial_belongings.get(actor, {})
        require(
            initial.get("chosen_item_id") == eid and not initial.get("item_ids"),
            "只能找回起始检定中确实遗失的本人随身物",
        )
        require(checks, "找回随身物需要本次实际幸运检定")
        instance = eid + ":" + actor
        require(instance not in runtime.item_instances, "该随身物已经找回，不能复制")
        runtime.item_instances[instance] = eid
        runtime.inventory[instance] = actor
        initial["recovered_item_id"] = instance
    elif op == "initial":
        require(actor not in runtime.initial_belongings, "起始随身物已经确定，不能重骰或重新选择")
        require(checks, "起始随身物需要实际幸运检定")
        instance = eid + ":" + actor
        require(
            instance not in runtime.inventory and instance not in runtime.consumed_items,
            "此物品已经分配",
        )
        runtime.initial_belongings[actor] = {
            "chosen_item_id": eid,
            "item_ids": [eid] if rule.check_passed else [],
            "check_ids": checks,
            "source_event_seq": seq,
        }
        if rule.check_passed:
            runtime.item_instances[instance] = eid
            runtime.inventory[instance] = actor
