"""Local 1907 PDF 27, 87–88, 92, 96, 99, 101–102; see docs/basic-combat.md."""

import re

from app.dice.service import parse_dice
from app.rules.compound import RANK


def damage_bounds(formula):
    if re.fullmatch(r"-?\d{1,5}", formula):
        return int(formula), int(formula)
    d = parse_dice(formula)
    return d.count + d.modifier, d.count * d.sides + d.modifier


def melee_result(attack, defense, mode):
    if not attack["passed"] and not defense["passed"]:
        return None
    a, d = RANK[attack["level"]], RANK[defense["level"]]
    if mode == "dodge":
        return "attack" if attack["passed"] and a > d else None
    return "attack" if attack["passed"] and a >= d else "defense" if defense["passed"] else None


def damage_plan(weapon, bonus, result, *, counter=False, difficulty="regular"):
    extra = result["level"] in {"critical", "extreme"} and not counter
    if weapon.kind == "firearm" and difficulty == "extreme":
        extra = result["level"] == "critical"
    formulas = [weapon.damage]
    if weapon.kind == "melee":
        formulas.append(bonus)
    if extra:
        maximum = sum(damage_bounds(f)[1] for f in formulas)
        return maximum, [weapon.damage] if weapon.impale else []
    return 0, formulas


def apply_injury(character, amount, *, armor_applies, key, reason, minute, round_number, day=0):
    """One entry for weapon, push and insanity damage. Snapshot-local application receipt."""
    injury = character.injury
    if key in injury.receipts:
        return injury.receipts[key]
    before = character.hp
    absorbed = min(amount, character.armor) if armor_applies else 0
    net = max(0, amount - absorbed)
    ignored = character.hp == 0 or injury.dead
    if not ignored:
        character.hp = max(0, before - net)
        if net > 0:
            injury.last_damage_minute = minute
            injury.first_aid_attempted = False
            injury.medicine_attempted = False
            injury.damage_day = day
        if net > character.hp_max:
            injury.dead = injury.unconscious = True
            injury.dying = injury.stabilized = False
            injury.con_pending = None
        elif 2 * net >= character.hp_max:
            injury.major_wound = injury.prone = True
            injury.con_pending = "wound" if character.hp else None
        if character.hp == 0 and not injury.dead:
            injury.unconscious = True
            injury.con_pending = None
            if injury.major_wound:
                injury.dying = True
                injury.stabilized = False
                injury.dying_since_round = round_number
    receipt = {
        "armor_applies": armor_applies,
        "key": key,
        "raw_damage": amount,
        "armor": absorbed,
        "damage": net,
        "hp_before": before,
        "hp_after": character.hp,
        "reason": reason,
        "ignored_at_zero": ignored,
        "major_wound": injury.major_wound,
        "unconscious": injury.unconscious,
        "dying": injury.dying,
        "dead": injury.dead,
    }
    injury.receipts[key] = receipt
    if net and not ignored:
        injury.wounds.append({**receipt, "minute": minute, "round": round_number})
    return receipt
