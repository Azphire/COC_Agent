"""Use the prepared inventory, the actor's own instance, and the MP service."""

from uuid import UUID

from app.preparation.inventory import held_instance
from app.rooms.resource_service import apply_mp, validate_mp, validate_time
from app.rooms.service import require


async def preflight_use(agents, session, room, state, rule, args, actor):
    effect = rule.use_effect
    instance = held_instance(state.module_runtime, rule.item_id, actor, args.item_instance_id)
    require(instance, "须明确选择本次行动者实际持有的一件物品")
    item = await agents.entities.entity(session, room.id, rule.item_id)
    require(item.entity_type == "item" and item.state != "hidden", "物品尚未实际发现")
    maximum = item.snapshot.get("item_uses")
    remaining = state.module_runtime.item_uses.get(instance, maximum)
    require(
        not effect.uses or remaining is not None and remaining >= effect.uses,
        "物品次数不足或尚未配置次数",
    )
    slots = {s.member_id: s for s in await agents.rooms.slots(session, room) if s.member_id}
    target_id = args.target_member_id or actor
    active = {m.id for m in await agents.rooms.members(session, room) if m.active}
    require(
        actor in slots and target_id in slots and target_id in active,
        "使用者和目标必须是当前房间调查员",
    )
    require(effect.target == "investigator" or target_id == actor, "该效果只能对本人使用")
    character = state.characters[UUID(slots[actor].id)]
    target = state.characters[UUID(slots[target_id].id)]
    require(
        not state.combat.active and not state.combat.pending_id,
        "物品效果尚未配置战斗动作经济，请先完成战斗结算",
    )
    require(
        not (character.injury.dead or character.injury.unconscious or character.injury.dying)
        and character.hp != 0,
        "使用者当前无法行动",
    )
    require(character.sanity.phase not in {"awaiting_symptom", "bout"}, "请先处理疯狂发作")
    require(not target.injury.dead, "此恢复效果不能作用于死者")
    validate_mp(character, effect.mp_cost)
    if effect.mp_restore:
        validate_mp(target, 0)
    if rule.elapsed_minutes:
        validate_time(state, state.game_minute + rule.elapsed_minutes)
    return instance, remaining, character, target, target_id


async def apply_use(agents, session, room, state, rule, args, actor, passed):
    instance, remaining, character, target, target_id = await preflight_use(
        agents, session, room, state, rule, args, actor
    )
    effect = rule.use_effect
    attempted_cost = passed or effect.consume_on_failure
    applied = effect.model_copy(
        update={
            "mp_cost": effect.mp_cost if attempted_cost else 0,
            "mp_restore": effect.mp_restore if passed else 0,
        }
    )
    changes = apply_mp(character, target, applied)
    used = effect.uses if attempted_cost else 0
    if remaining is not None:
        state.module_runtime.item_uses[instance] = remaining - used
    if remaining is not None and remaining - used == 0:
        state.module_runtime.inventory.pop(instance)
        state.module_runtime.consumed_items[instance] = (
            state.module_runtime.consumed_items.get(instance, 0) + used
        )
        if instance in state.module_runtime.sounds:
            state.module_runtime.sounds[instance]["active"] = False
    return {
        "effect_id": effect.id,
        "basis": effect.basis,
        "item_instance_id": instance,
        "target_member_id": target_id,
        "passed": passed,
        "uses_before": remaining,
        "uses_after": remaining - used if remaining is not None else None,
        "minute": state.game_minute,
        **changes,
    }
