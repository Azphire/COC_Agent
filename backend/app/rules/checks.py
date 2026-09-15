"""Verified local CoC7 check subset. See definitions/SOURCES.md, batch 3."""

from app.dice.service import DiceService

# Local CoC7 rulebook PDF 68–69. Older frozen cards predate this minimal subset.
MODULE_BASE_SKILLS = {"stealth": 20, "throw": 20}


def available_skills(snapshot):
    values = snapshot.get("skill_values", {})
    if snapshot.get("ruleset_id") == "coc7-character-creation":
        return {**MODULE_BASE_SKILLS, **values}
    return values


def prompt_skills(snapshot, action=""):
    """Keep the expanded catalogue from crowding out facts in an 8192 context.

    Full values stay in the immutable snapshot and check validator and are
    available through inspect_character. Show the established common skills
    plus chosen, trained, occupational or explicitly named new skills.
    """
    values = available_skills(snapshot)
    if snapshot.get("ruleset_version") != "1.1.0":
        return values
    from app.rules.loader import archived_ruleset, load_rulesets

    rules = load_rulesets().get(snapshot.get("ruleset_id"))
    if not rules:
        return values
    common = {s.key for s in archived_ruleset(rules.id, "1.0.0").skills}
    chosen = set(snapshot.get("selected_specializations", []))
    for keys in snapshot.get("occupation_group_choices", {}).values():
        chosen.update(keys)
    occupation = next((o for o in rules.occupations if o.key == snapshot.get("occupation")), None)
    if occupation:
        chosen.update(occupation.fixed_skills)
    for field in ("occupation_skills", "interest_skills"):
        chosen.update(k for k, v in snapshot.get(field, {}).items() if v.get("points"))
    return {
        s.key: values[s.key]
        for s in rules.skills
        if s.key in values
        and (
            s.key in common
            or s.key in chosen
            or s.key in action
            or s.display_name.split("（")[-1].rstrip("）") in action
        )
    }


def check_value(snapshot: dict, kind: str, name: str) -> int:
    if snapshot.get("ruleset_id") != "coc7-character-creation":
        raise ValueError("检定仅支持已核对的第七版角色")
    source = snapshot["effective_attributes"] if kind == "attribute" else available_skills(snapshot)
    if name not in source or type(source[name]) is not int or not 0 <= source[name] <= 999:
        raise ValueError("角色快照中没有此属性或技能")
    return source[name]


def threshold(value: int, difficulty: str) -> int:
    return value // {"regular": 1, "hard": 2, "extreme": 5}[difficulty]


def judge(value: int, difficulty: str, total: int) -> dict:
    if not 0 <= value <= 999 or not 1 <= total <= 100:
        raise ValueError("检定数值不合法")
    target = threshold(value, difficulty)
    if total == 1:
        level = "critical"
    elif total == 100 or (target < 50 and total >= 96):
        level = "fumble"
    elif total <= value // 5:
        level = "extreme"
    elif total <= value // 2:
        level = "hard"
    elif total <= value:
        level = "regular"
    else:
        level = "failure"
    passed = level == "critical" or (level != "fumble" and total <= target)
    return {
        "total": total,
        "threshold": target,
        "level": level,
        "passed": passed,
        "outcome": "critical"
        if level == "critical"
        else "fumble"
        if level == "fumble"
        else "success"
        if passed
        else "failure",
    }


def roll_check(dice: DiceService, value: int, difficulty: str, bonus: int, penalty: int):
    if (
        type(bonus) is not int
        or type(penalty) is not int
        or not (0 <= bonus <= 2 and 0 <= penalty <= 2)
    ):
        raise ValueError("奖惩骰数量必须为 0–2")
    net = bonus - penalty
    # Map the existing service's uniform 1..10 dice to 0..9. A 00+0 pair is 100.
    raw = dice.roll(f"{2 + abs(net)}d10", "check")
    units, *tens = [x % 10 for x in raw.dice]
    candidates = [(x * 10 + units) or 100 for x in tens]
    total = min(candidates) if net >= 0 else max(candidates)
    detail = {
        "units": units,
        "tens": tens,
        "candidates": candidates,
        "selected": total,
        "net_bonus": net,
        "roll_record": raw.model_dump(mode="json"),
    }
    return detail, judge(value, difficulty, total)
