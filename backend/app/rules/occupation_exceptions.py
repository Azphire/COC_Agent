"""Per-card, source-backed occupation options; keeper decisions stay local.

Drafts preview the candidate effect before approval. Only keeper_approval issues may
pass submission preview; finalization still requires an exact local decision.
"""

import hashlib
import json


def occupation_for(character, ruleset):
    return next((o for o in ruleset.occupations if o.key == character.occupation), None)


def mythos_option(character, occupation, group):
    return bool(
        occupation.initial_mythos
        and character.initial_mythos_proposal
        and character.initial_mythos_proposal.source == occupation.key
        and character.era in occupation.eras
        and group.key == occupation.initial_mythos.selection_group
    )


def apply_occupation_options(character, ruleset, occupation, allowed, issue):
    """Return candidate membership without changing templates or transferring points."""
    allowed = set(allowed)
    proposal = character.occupation_skill_replacement
    if proposal:
        field = "occupation_skill_replacement"
        policy = occupation.skill_replacement if occupation else None
        if not policy or character.era not in occupation.eras:
            issue(field, "unsupported", "此职业、年代或规则版本不支持职业技能替换")
        elif not proposal.reason.strip():
            issue(field, "required", "请说明替换理由")
        elif proposal.replacement_skill != policy.target_skill:
            issue(field, "selection", "此条款仅允许以催眠替换一个职业技能")
        elif proposal.original_skill == "credit_rating" or proposal.original_skill not in allowed:
            issue(field, "selection", "原技能须是当前固定或已选职业技能，不能替换信用评级")
        elif proposal.replacement_skill in allowed:
            issue(field, "duplicate", "替换目标已占职业名额，不能重复计数")
        else:
            allowed.remove(proposal.original_skill)
            allowed.add(proposal.replacement_skill)

    character.initial_mythos = 0
    proposal = character.initial_mythos_proposal
    if proposal:
        field = "initial_mythos_proposal"
        policy = occupation.initial_mythos if occupation else None
        if not policy or proposal.source != occupation.key or character.era not in occupation.eras:
            issue(field, "unsupported", "此职业、年代或规则版本不支持该初始神话来源")
        elif not proposal.reason.strip():
            issue(field, "required", "请说明初始神话获取理由")
        elif character.occupation_group_choices.get(policy.selection_group) != ["cthulhu_mythos"]:
            issue(field, "selection", "初始神话须占用个人特长的一项选择名额")
        else:
            character.initial_mythos = proposal.value
    character.effective_occupation_skills = sorted(allowed)
    validate_approvals(character, ruleset, issue)
    return allowed


def review_requirements(character, ruleset):
    occupation = occupation_for(character, ruleset)
    if not occupation or character.era not in occupation.eras:
        return {}
    requirements = {}
    for key, proposal, policy in (
        ("skill_replacement", character.occupation_skill_replacement, occupation.skill_replacement),
        ("initial_mythos", character.initial_mythos_proposal, occupation.initial_mythos),
    ):
        if not proposal or not policy:
            continue
        # Bind the rule semantics as well as its published version and exact choice.
        payload = [
            ruleset.id,
            ruleset.version,
            occupation.model_dump(mode="json"),
            character.era,
            character.occupation_group_choices,
            character.selected_occupation_skills,
            proposal.model_dump(mode="json"),
            {k: v.value for k, v in character.attributes.items()}
            if key == "initial_mythos"
            else None,
        ]
        token = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        requirements[key] = (token, policy.source, policy.note)
    return requirements


def approve_occupation_exceptions(character, ruleset, keys):
    requirements = review_requirements(character, ruleset)
    if len(keys) != len(set(keys)) or not set(keys) <= requirements.keys():
        raise ValueError("只能核准当前职业、年代和规则版本支持的已选职业特例")
    character.occupation_exception_approvals = {
        key: token
        for key, (token, _, _) in requirements.items()
        if key in keys or character.occupation_exception_approvals.get(key) == token
    }


def validate_approvals(character, ruleset, issue):
    requirements = review_requirements(character, ruleset)
    character.occupation_exception_approvals = {
        key: token
        for key, (token, _, _) in requirements.items()
        if character.occupation_exception_approvals.get(key) == token
    }
    for key, (_, source, note) in requirements.items():
        if key not in character.occupation_exception_approvals:
            issue(
                f"occupation_exception_approvals.{key}",
                "keeper_approval",
                f"待 KP 明确核准：{note}（{source}）",
            )


def apply_initial_mythos(character):
    # This is a derivation from the original attributes, never a stored subtraction.
    # The occultist footnote specifies no equal SAN loss (unlike the experience pack).
    if character.initial_mythos:
        character.skill_values["cthulhu_mythos"] = character.initial_mythos
        maximum = 99 - character.initial_mythos
        character.derived_values["san_max"] = maximum
        if "san" in character.derived_values:
            character.derived_values["san"] = min(character.derived_values["san"], maximum)
