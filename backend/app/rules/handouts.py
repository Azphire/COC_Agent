"""Pure, repeatable HO derivations from base allocations and reviewed definitions."""

import hashlib
import json

from app.domain.handouts import HandoutEffect


def review_token(character, ruleset):
    if character.module_handout is None:
        return None
    # Local consent covers the exact source definition, choice and base build.
    fields = {
        "module_handout", "age", "era", "occupation", "attributes", "age_deductions",
        "occupation_skills", "interest_skills", "experience_skills", "experience",
        "occupation_attribute", "occupation_group_choices", "selected_occupation_skills",
        "background",
    }
    payload = [ruleset.id, ruleset.version, character.model_dump(mode="json", include=fields)]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def approve_module_handout(character, ruleset):
    if character.module_handout is None:
        raise ValueError("请先选择有来源的 HO 角色方案")
    character.module_handout_approval = review_token(character, ruleset)


def apply_attributes(character, ruleset, issue):
    character.module_handout_effects = []
    handout = character.module_handout
    if handout is None:
        character.module_handout_approval = None
        return
    token = review_token(character, ruleset)
    if character.module_handout_approval != token:
        character.module_handout_approval = None
        issue("module_handout_approval", "keeper_approval", "待 KP 核准当前 HO 来源和角色调整方案")
    policy = handout.definition.adjustments
    if policy is None:
        if handout.attribute_allocations:
            issue("module_handout.attribute_allocations", "forbidden", "此 HO 没有属性加点")
        return
    if policy.ruleset_id != ruleset.id:
        issue("module_handout", "ruleset", "此 HO 不适用于当前规则集")
        return
    for field, required in (
        ("age", policy.required_age), ("occupation", policy.required_occupation),
        ("era", policy.required_era),
    ):
        if required is not None and getattr(character, field) != required:
            issue(field, "handout_requirement", f"HO 角色要求 {field} = {required}")
    allocation = handout.attribute_allocations
    choices = set(policy.attribute_choices)
    if set(allocation) - choices:
        issue("module_handout.attribute_allocations", "selection", "属性加点不在 HO 允许范围内")
    if sum(allocation.values()) != policy.attribute_points:
        issue(
            "module_handout.attribute_allocations", "allocation",
            f"HO 属性点必须恰好分配 {policy.attribute_points} 点",
        )
    for key, amount in allocation.items():
        if key not in choices or key not in character.effective_attributes:
            continue
        base = character.effective_attributes[key]
        final = base + amount
        character.effective_attributes[key] = final
        character.module_handout_effects.append(HandoutEffect(
            target="attribute", key=key, base_value=base, adjustment=amount,
            final_value=final, source_pages=handout.definition.source_pages,
        ))
        maximum = policy.attribute_maxima.get(key, policy.attribute_maximum)
        if maximum is not None and final > maximum:
            issue(
                f"module_handout.attribute_allocations.{key}", "maximum",
                f"HO 调整后 {key.upper()} 不能超过 {maximum}，请重新分配",
            )


def apply_skills(character, ruleset, definitions, issue):
    handout = character.module_handout
    if handout is None or handout.definition.adjustments is None:
        return
    policy = handout.definition.adjustments
    if policy.ruleset_id != ruleset.id:
        return
    for key, amount in policy.skill_bonuses.items():
        if key not in character.skill_values or key not in definitions:
            issue(f"module_handout.{key}", "unknown", "HO 加值指定了当前规则中不存在的技能")
            continue
        base = character.skill_values[key]
        final = base + amount
        character.skill_values[key] = final
        character.module_handout_effects.append(HandoutEffect(
            target="skill", key=key, base_value=base, adjustment=amount,
            final_value=final, source_pages=handout.definition.source_pages,
        ))
        maximum = min(policy.skill_maximum, definitions[key].maximum)
        if final > maximum:
            issue(
                f"module_handout.{key}", "maximum",
                f"HO 调整后技能不能超过 {maximum}，请减少基础技能投入",
            )
    if policy.credit_maximum is not None:
        if character.skill_values.get("credit_rating", 0) > policy.credit_maximum:
            issue(
                "module_handout.credit_rating", "maximum",
                f"HO 要求最终信用评级不超过 {policy.credit_maximum}",
            )
