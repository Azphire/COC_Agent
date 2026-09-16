"""Four verified creation packages, independent pools and local keeper consent.

No runtime effect comes from a player label. Runtime matches a frozen, approved
package against an exclusively classified, prepared SAN cause.
"""

import hashlib
import json

from app.dice.service import parse_dice

CATEGORIES = {
    "witness_corpse": "目击尸体",
    "witness_severe_injury": "目击重伤员",
    "witness_murder": "目击谋杀",
    "commit_murder": "亲自谋杀",
    "witness_human_mutilation": "目击对人类的暴力残害",
}


def policy_for(character, ruleset):
    return next(
        (
            p
            for p in ruleset.experience_packages
            if character.experience and p.key == character.experience.package
        ),
        None,
    )


def prepare_roll(character, ruleset, dice):
    policy = policy_for(character, ruleset)
    if policy and policy.key not in character.experience_rolls:
        record = dice.roll(policy.san_loss, "experience_" + policy.key)
        record.purpose = "experience_san"
        character.experience_rolls[policy.key] = record


def validate_rolls(character, ruleset, issue):
    policies = {p.key: p for p in ruleset.experience_packages}
    ids = [r.id for r in character.roll_records] + [
        r.id for r in character.experience_rolls.values()
    ]
    if len(ids) != len(set(ids)):
        issue("experience_rolls", "duplicate", "原骰记录ID不得重复")
    for key, record in character.experience_rolls.items():
        policy = policies.get(key)
        if not policy:
            issue("experience_rolls", "unsupported", "此规则版本没有该经历包骰来源")
            continue
        dice = parse_dice(policy.san_loss)
        if not (
            record.attribute == "experience_" + key
            and record.purpose == "experience_san"
            and record.formula == policy.san_loss
            and record.multiplier == 1
            and record.modifier == dice.modifier
            and len(record.dice) == dice.count
            and all(1 <= n <= dice.sides for n in record.dice)
            and record.total == sum(record.dice) + dice.modifier
        ):
            issue("experience_rolls", "inconsistent", "经历包原骰与来源、骰式或数值不一致")


def review_requirements(character, ruleset):
    policy = policy_for(character, ruleset)
    if not policy:
        return {}
    payload = {
        key: getattr(character, key)
        for key in (
            "ruleset_id",
            "ruleset_version",
            "age",
            "era",
            "occupation",
            "occupation_attribute",
            "occupation_group_choices",
            "selected_occupation_skills",
            "selected_specializations",
            "age_deductions",
        )
    }
    for key in ("attributes", "experience_skills", "occupation_skills", "interest_skills"):
        payload[key] = {k: v.model_dump(mode="json") for k, v in getattr(character, key).items()}
    payload.update(
        rule_id=ruleset.id,
        rule_version=ruleset.version,
        policy=policy.model_dump(mode="json"),
        experience=character.experience.model_dump(mode="json"),
        custom=[s.model_dump() for s in character.custom_specializations],
        mythos=character.initial_mythos_proposal.model_dump()
        if character.initial_mythos_proposal
        else None,
        replacement=character.occupation_skill_replacement.model_dump()
        if character.occupation_skill_replacement
        else None,
        # Existing age rolls determine EDU. Bind them without changing their validation.
        rolls=[r.model_dump(mode="json") for r in character.roll_records],
        package_roll=character.experience_rolls[policy.key].model_dump(mode="json")
        if policy.key in character.experience_rolls
        else None,
    )
    return {
        policy.key: hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
    }


def approve_experience(character, ruleset, keys):
    requirements = review_requirements(character, ruleset)
    if len(keys) != len(set(keys)) or not set(keys) <= requirements.keys():
        raise ValueError("只能核准当前已选且此规则版本支持的经历包")
    character.experience_approvals = {
        key: token
        for key, token in requirements.items()
        if key in keys or character.experience_approvals.get(key) == token
    }


def evaluate(character, ruleset, definitions, issue):
    from app.rules.character_options import group_options

    character.experience_effects = {}
    validate_rolls(character, ruleset, issue)
    policy = policy_for(character, ruleset)
    requirements = review_requirements(character, ruleset)
    character.experience_approvals = {
        k: v for k, v in requirements.items() if character.experience_approvals.get(k) == v
    }
    if not character.experience:
        return 0, set()
    if not policy:
        issue("experience", "unsupported", "此规则版本不支持所选经历包")
        return 0, set()
    selection = character.experience
    if policy.minimum_age and (character.age or 0) < policy.minimum_age:
        issue("experience", "age", f"{policy.display_name}初始年龄不能低于{policy.minimum_age}岁")
    if policy.occupations and character.occupation not in policy.occupations:
        issue("experience", "occupation", "医务经历须为资深医生、护士或法医职业")
    if not selection.history.strip() or not selection.background_detail.strip():
        issue("experience", "background", "须填写资格经历及相关伤疤／恐惧症／躁狂症背景")
    if policy.key == "war":
        year, now, age = selection.war_year, selection.scenario_year, selection.age_at_war
        if year is None or now is None or age is None or year > now:
            issue("experience", "chronology", "须填写参战年份、参战年龄和不早于参战的模组年份")
        elif age + now - year != character.age:
            issue(
                "experience",
                "chronology",
                "当前年龄须等于参战年龄加年份差；请以最终年龄创建角色，原年龄骰不重掷",
            )
        if now is not None and character.era == "1920s" and not 1920 <= now <= 1929:
            issue("experience", "era", "1920年代角色的模组年份须在1920–1929年")
    elif any(
        v is not None for v in (selection.war_year, selection.scenario_year, selection.age_at_war)
    ):
        issue("experience", "selection", "此包不使用战场年份或加龄条款")
    variant = policy.variants.get(selection.variant)
    allowed = set()
    if not variant:
        issue("experience.variant", "selection", "请选择此包支持的经历身份")
    else:
        allowed.update(variant.fixed_skills)
        allowed.update(
            s.key
            for s in definitions.values()
            if s.specialization_group in variant.specialty_groups
            and s.allocatable
            and character.era in s.eras
            and s.key in character.selected_specializations
        )
        if selection.choices.keys() - {g.key for g in variant.skill_groups}:
            issue("experience.choices", "selection", "经历包包含无来源的技能组")
        for group in variant.skill_groups:
            chosen = selection.choices.get(group.key, [])
            field = "experience.choices." + group.key
            options = group_options(group, ruleset, character.era, definitions.values())
            if len(chosen) != group.count:
                issue(field, "selection_count", f"{group.display_name}须选择{group.count}项")
            if len(chosen) != len(set(chosen)) or set(chosen) & allowed:
                issue(field, "duplicate", "经历包专业或技能不能重复占位")
            if not set(chosen) <= options:
                issue(field, "selection", "经历包选择不在原文技能白名单内")
            allowed.update(set(chosen) & options)
    record = character.experience_rolls.get(policy.key)
    if not record:
        issue("experience_rolls", "incomplete", "缺少经历包原骰；导入不能补掷或推定骰点")
    for key in requirements.keys() - character.experience_approvals.keys():
        issue(
            "experience_approvals." + key,
            "keeper_approval",
            f"待KP核对资格、{policy.points}点技能投入、SAN代价及限定免疫：{policy.source}",
        )
    character.experience_effects = {
        "package": policy.key,
        "name": policy.display_name,
        "source": policy.source,
        "pool": policy.points,
        "allowed_skills": sorted(allowed),
        "san_loss": record.total if record else 0,
        "immunity": list(policy.immunity),
        "immunity_reasons": [CATEGORIES[c] for c in policy.immunity],
        "background_kind": selection.background_kind,
        "background_detail": selection.background_detail,
        "approved": bool(character.experience_approvals),
    }
    return policy.points, allowed


def apply_san(character):
    effects = character.experience_effects
    if not effects or "pow" not in character.effective_attributes:
        return
    before = character.effective_attributes["pow"]
    maximum = max(0, 99 - character.initial_mythos)
    # Subtract the package loss from initial POW, then enforce the mythos ceiling.
    after = min(maximum, max(0, before - effects["san_loss"]))
    character.derived_values["san"] = after
    effects.update(san_before=before, san_after=after, san_max=maximum)


def matching_immunity(snapshot, effect):
    from app.domain.character import CharacterSheet
    from app.rules.loader import runtime_ruleset

    category = effect.experience_category
    if category is None or not snapshot.get("experience"):
        return None
    character = CharacterSheet.model_validate(snapshot)
    ruleset = runtime_ruleset(character.ruleset_id, character.ruleset_version)
    policy = policy_for(character, ruleset) if ruleset else None
    if not policy or category not in policy.immunity:
        return None
    required = review_requirements(character, ruleset)
    if not required or character.experience_approvals != required:
        return None
    return {
        "kind": "experience",
        "package": policy.key,
        "source": policy.source,
        "category": category,
        "reason": CATEGORIES[category],
    }
