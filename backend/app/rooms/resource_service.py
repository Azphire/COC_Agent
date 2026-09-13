"""Configured MP effects and game-time recovery, inside the room transaction.

CoC7 local rulebook 1907, physical PDF page 148 (Magic points): one point
per game hour, another per 100 POW; natural recovery is capped at POW/5.
No wall clock, chat counter, HP substitution, or model-authored deltas.
"""

from uuid import UUID

from app.rooms.service import require

MP_RULE = "coc7.mp_recovery:rulebook1907:pdf148"


def complete_resources(state, slots):
    for slot in slots:
        character = state.characters.get(UUID(slot.id))
        snapshot = slot.character_snapshot
        if not character or snapshot.get("ruleset_id") != "coc7-character-creation":
            continue
        if character.mp_max is None:
            character.mp_max = snapshot.get("derived_values", {}).get("mp")
            power = snapshot.get("effective_attributes", {}).get("pow")
            character.mp_recovery_per_hour = 1 + power // 100 if power is not None else 0
            # Old saves have no recovery history: begin at their saved minute,
            # preserving current MP and giving no retroactive recovery credit.
        if character.hp_max is None:
            character.hp_max = snapshot.get("derived_values", {}).get("hp")


def validate_time(state, minute, *, mode="effect", treating=None):
    require(state.game_minute <= minute <= 1_000_000, "游戏时间不能倒退或超出上限")
    if minute == state.game_minute:
        return
    if mode in {"effect", "push"}:
        require(not state.combat.active and not state.combat.pending_id, "请先完成战斗或伤势结算")
        require(
            not any(
                c.sanity.phase in {"awaiting_symptom", "bout"} for c in state.characters.values()
            ),
            "请先处理疯狂发作，不能用时间效果跳过",
        )
    injuries = [(p.id, p.injury) for p in state.combat.participants.values()]
    injuries += [
        (str(sid), c.injury)
        for sid, c in state.characters.items()
        if not any(p.slot_id == str(sid) for p in state.combat.participants.values())
    ]
    for pid, injury in injuries:
        if injury.dead:
            continue
        require(not injury.con_pending, "请先完成必要体质检定")
        require(not injury.dying or injury.stabilized, "濒死未稳定时须逐轮处理")
        if injury.stabilized and injury.check_due_minute is not None and pid != treating:
            require(minute <= injury.check_due_minute, "不能越过下一次必要体质检定时间")


def advance_time(state, minute, *, key, source, mode="effect", treating=None):
    previous = state.time_receipts.get(key)
    if previous:
        require(
            previous["minute_after"] == minute and previous["source"] == source,
            "时间事件ID已用于其他推进",
        )
        return previous
    validate_time(state, minute, mode=mode, treating=treating)
    before_minute = state.game_minute
    changes = []
    for sid, c in state.characters.items():
        before, progress = c.mp, c.mp_recovery_progress
        if c.mp is None or c.mp_max is None or not c.mp_recovery_per_hour or c.injury.dead:
            continue
        if c.mp >= c.mp_max:
            c.mp_recovery_progress = 0
        else:
            hours, c.mp_recovery_progress = divmod(progress + minute - before_minute, 60)
            c.mp = min(c.mp_max, c.mp + hours * c.mp_recovery_per_hour)
            if c.mp == c.mp_max:
                c.mp_recovery_progress = 0
        if c.mp != before or c.mp_recovery_progress != progress:
            changes.append(
                {
                    "slot_id": str(sid),
                    "before": before,
                    "after": c.mp,
                    "progress_before": progress,
                    "progress_after": c.mp_recovery_progress,
                    "rule": MP_RULE,
                }
            )
    state.game_minute = minute
    for p in state.combat.participants.values():
        if p.injury.stabilized and p.injury.check_due_minute is not None:
            if p.injury.check_due_minute <= minute and p.id != treating:
                p.injury.con_pending = "hourly"
    for sid, c in state.characters.items():
        if not any(p.slot_id == str(sid) for p in state.combat.participants.values()):
            if (
                c.injury.stabilized
                and c.injury.check_due_minute is not None
                and c.injury.check_due_minute <= minute
            ):
                c.injury.con_pending = "hourly"
    receipt = {
        "source": source,
        "minute_before": before_minute,
        "minute_after": minute,
        "resource_changes": changes,
    }
    state.time_receipts[key] = receipt
    return receipt


def validate_mp(character, cost):
    require(character.mp is not None and character.mp_max is not None, "角色尚无已核对的MP及上限")
    require(character.mp >= cost, "MP不足，未配置HP代付")


def apply_mp(actor, target, effect):
    validate_mp(actor, effect.mp_cost)
    if effect.mp_restore:
        validate_mp(target, 0)
    before_actor, before_target = actor.mp, target.mp
    actor.mp -= effect.mp_cost
    if effect.mp_restore:
        target.mp = max(target.mp, min(target.mp_max, target.mp + effect.mp_restore))
    if target.mp == target.mp_max:
        target.mp_recovery_progress = 0
    return {
        "cost": effect.mp_cost,
        "restore": effect.mp_restore,
        "actor_before": before_actor,
        "actor_after": actor.mp,
        "target_before": before_target,
        "target_after": target.mp,
    }
