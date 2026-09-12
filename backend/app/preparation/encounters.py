"""The prepared train encounter: persistent sound, contact and sourced combat bases."""

from app.rooms.service import require


def clear_inactive_grips(state):
    from app.rooms.combat_service import capable

    for actor, grips in list(state.module_runtime.grapples.items()):
        live = [
            pid
            for pid in grips
            if pid not in state.combat.participants or capable(state.combat.participants[pid])
        ]
        if live:
            state.module_runtime.grapples[actor] = live
        else:
            state.module_runtime.grapples.pop(actor)


def can_locate(state, actor, target):
    if "blind_sound_tracking" not in actor.traits:
        return True
    if actor.id in state.module_runtime.grapples.get(target.id, []):
        return True
    focus = state.module_runtime.sound_attention.get(actor.id)
    if focus:
        sound = state.module_runtime.sounds.get(focus, {})
        return bool(
            sound.get("active")
            and sound.get("actor_id") == target.id
            and sound.get("scene_node_id") == actor.scene_node_id
        )
    return any(
        s.get("active")
        and s.get("actor_id") == target.id
        and s.get("scene_node_id") == actor.scene_node_id
        for s in state.module_runtime.sounds.values()
    )


def record_combat_sound(state, actor):
    state.module_runtime.sounds["combat:" + actor.id] = {
        "actor_id": actor.id,
        "scene_node_id": actor.scene_node_id,
        "active": True,
        "continuous": False,
        "kind": "combat",
    }
    direct_attention(state, actor.scene_node_id, "combat:" + actor.id)


def direct_attention(state, node, sound_id):
    for npc in state.combat.participants.values():
        if "blind_sound_tracking" in npc.traits and npc.scene_node_id == node:
            state.module_runtime.sound_attention[npc.id] = sound_id


def end_transient_sound(state, node):
    """After using the passage window, return to any remaining persistent source."""
    runtime = state.module_runtime
    for sound in runtime.sounds.values():
        if sound.get("scene_node_id") == node and not sound.get("continuous"):
            sound["active"] = False
    remaining = next(
        (
            key
            for key, sound in reversed(list(runtime.sounds.items()))
            if sound.get("active") and sound.get("scene_node_id") == node
        ),
        None,
    )
    for npc in state.combat.participants.values():
        if "blind_sound_tracking" in npc.traits and npc.scene_node_id == node:
            runtime.sound_attention.pop(npc.id, None)
            if remaining:
                runtime.sound_attention[npc.id] = remaining
    runtime.flags["continuous_sound"] = any(
        s.get("active") and s.get("continuous") for s in runtime.sounds.values()
    )
    if not runtime.flags.get("clickers_blocked") and not remaining:
        runtime.flags["safe_passage"] = False
        runtime.flags["clickers_lured"] = False


async def freeze_combat_basis(agents, session, room, state, participant, basis):
    if not participant.member_id:
        return  # The manuscript's environment correction applies to investigators.
    nav = await agents.navigation.state(session, room.id)
    if not nav:
        return
    candidates = []
    for entity in await agents.entities.rows(session, room.id):
        for rule in entity.snapshot.get("check_adjustments", []):
            if (
                rule.get("combat_only")
                and rule["name"] in {basis["name"], "*"}
                and (
                    not rule.get("scene_node_ids")
                    or nav.current_scene_node_id in rule["scene_node_ids"]
                )
                and all(
                    state.module_runtime.flags.get(k, False) == v
                    for k, v in rule.get("required_flags", {}).items()
                )
            ):
                candidates.append((entity.source_entity_id, rule))
    require(len(candidates) <= 1, "战斗环境修正尚未唯一确定", 422)
    if candidates:
        eid, rule = candidates[0]
        base = basis["value"]
        basis["value"] = max(
            0,
            min(
                999,
                base * rule.get("numerator", 1) // rule.get("denominator", 1) + rule.get("add", 0),
            ),
        )
        basis["module_adjustment"] = {
            "source_entity_id": eid,
            "rule": rule,
            "base_value": base,
            "value": basis["value"],
        }


def apply_encounter(state, rule, args, actor, node, seq):
    runtime, op = state.module_runtime, rule.encounter_operation
    if not op:
        return
    if op.startswith("sound_"):
        from app.preparation.inventory import held_instance

        item_id = rule.sound_item_id or args.used_item_id
        sound_id = held_instance(runtime, item_id, actor) if item_id else None
        if item_id:
            require(
                sound_id,
                "声源物品必须实际在场",
            )
        if op == "sound_once":
            require(sound_id or rule.allow_worn_sound_item, "须明确这次实际投出的持有物")
            if sound_id:
                runtime.inventory.pop(sound_id)
                runtime.dropped_items[sound_id] = node
            else:
                sound_id = "worn:" + actor
                require(sound_id not in runtime.sounds, "这件已投出的随身穿戴物不能再次投出")
        sound_id = sound_id or actor
        old = runtime.sounds.get(sound_id, {})
        require(op != "sound_stop" or old.get("active"), "声源尚未开启")
        runtime.sounds[sound_id] = {
            "actor_id": None if op == "sound_once" else actor,
            "scene_node_id": node,
            "source_event_seq": seq,
            "active": op != "sound_stop",
            "continuous": op == "sound_start",
            "kind": "impact" if op == "sound_once" else "item",
        }
        if op == "sound_stop":
            end_transient_sound(state, node)
        else:
            direct_attention(state, node, sound_id)
        if op == "sound_stop" and not runtime.flags.get("clickers_blocked"):
            runtime.flags["safe_passage"] = False
            runtime.flags["clickers_lured"] = False
    elif op == "continuous_lure":
        sources = [
            key
            for key, sound in runtime.sounds.items()
            if sound.get("active")
            and sound.get("continuous")
            and sound.get("scene_node_id") == node
            and sound.get("actor_id") is None
            and runtime.dropped_items.get(key) == node
        ]
        require(sources, "持续诱导需要已放在当前场景、仍在响的实际声源")
        direct_attention(state, node, sources[-1])
    elif op in {"close_door", "open_door"}:
        require(rule.door_id, "缺少批准门实体")
        runtime.doors[rule.door_id] = op == "close_door"
    elif op == "grab":
        require(rule.npc_id and args.npc_instance_id, "需指定实际接触的敌人实例")
        instances = {
            f"module:{rule.npc_id}:{i + 1}" for i in range(runtime.npc_counts.get(rule.npc_id, 0))
        }
        require(
            args.npc_instance_id in instances and runtime.npc_locations.get(rule.npc_id) == node,
            "抓握敌人不在场",
        )
        from app.rooms.combat_service import capable

        opponent = state.combat.participants.get(args.npc_instance_id)
        require(not opponent or capable(opponent), "失去行动能力的敌人不能抓握")
        require(
            not any(
                args.npc_instance_id in ids for mid, ids in runtime.grapples.items() if mid != actor
            ),
            "敌人已抓住另一角色",
        )
        runtime.grapples[actor] = list(
            dict.fromkeys([*runtime.grapples.get(actor, []), args.npc_instance_id])
        )
    elif op == "release":
        grabbed = runtime.grapples.get(actor, [])
        require(len(grabbed) == 1, "只有实际被一只循声者抓住时可做力量挣脱")
        runtime.grapples.pop(actor)
    elif op == "carry":
        require(rule.npc_id and runtime.npc_locations.get(rule.npc_id) == node, "伤员不在场")
        patient = next(
            (p for p in state.combat.participants.values() if p.npc_id == rule.npc_id), None
        )
        require(not patient or not patient.injury.dead, "伤员已经死亡，不能记为获救随行")
        require(
            not runtime.carriers.get(rule.npc_id) or runtime.carriers[rule.npc_id] == actor,
            "伤员已由其他人背负",
        )
        runtime.carriers[rule.npc_id] = actor
        runtime.carry_checks[rule.npc_id] = {"scene_node_id": node, "minute": state.game_minute}
    elif op == "put_down":
        require(runtime.carriers.get(rule.npc_id) == actor, "只能放下本人背负的伤员")
        runtime.carriers.pop(rule.npc_id)
        runtime.following_npc_ids = [eid for eid in runtime.following_npc_ids if eid != rule.npc_id]
    elif op == "carry_check":
        require(runtime.carriers.get(rule.npc_id) == actor, "本次行动者没有背负伤员")
        runtime.carry_checks[rule.npc_id] = {"scene_node_id": node, "minute": state.game_minute}
