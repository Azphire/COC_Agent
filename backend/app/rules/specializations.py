"""Build per-character definitions without changing the shared skill catalogue."""

import hashlib
import json

from app.domain.specializations import normalized_name

SPECIALIZATION_LABELS = {
    "language": "外语",
    "art_craft": "艺术与手艺",
    "science": "科学",
    "pilot": "驾驶",
    "survival": "生存",
    "lore": "学问",
}


def character_skills(character, ruleset, issue=lambda *_: None):
    definitions = {s.key: s for s in ruleset.skills}
    groups = ruleset.custom_specialization_templates
    names = {
        (s.specialization_group, normalized_name(s.display_name.split("（")[-1].rstrip("）")))
        for s in ruleset.skills
        if s.specialization_group
    }
    for index, custom in enumerate(character.custom_specializations):
        field = f"custom_specializations.{index}"
        template = definitions.get(groups.get(custom.group))
        if template is None:
            issue(field, "unsupported", "此规则版本不支持该类别的自定义专业")
            continue
        name = (custom.group, normalized_name(custom.name))
        if custom.id in definitions or name in names:
            issue(field, "duplicate", "专业 ID 或名称重复；已有目录专业请直接选择")
            continue
        names.add(name)
        policy = ruleset.specialization_policies.get(custom.group)
        if policy:
            if name[1] in {normalized_name(n) for n in policy.forbidden_names}:
                issue(field, "forbidden_name", "此名称属于独立规则技能，不能以自定义专业代替")
            if character.era != "modern" and any(
                normalized_name(n) in name[1] for n in policy.modern_name_fragments
            ):
                issue(field, "era", "此驾驶专业仅适用于现代；1920年代请按规则选择当时的载具")
        definitions[custom.id] = template.model_copy(
            update={
                "key": custom.id,
                "display_name": template.display_name.split("（")[0] + f"（{custom.name}）",
            }
        )
    return definitions


def snapshot_skill_names(snapshot):
    return {
        s["id"]: f"{SPECIALIZATION_LABELS[s['group']]}（{s['name']}）"
        for s in snapshot.get("custom_specializations", [])
        if s["id"] in snapshot.get("skill_values", {}) and s["group"] in SPECIALIZATION_LABELS
    }


def review_requirements(character, ruleset, definitions=None):
    """Only configured exceptional skills need a local keeper's introduction decision."""
    definitions = definitions or character_skills(character, ruleset)
    chosen = set(character.selected_specializations)
    chosen.update(s.id for s in character.custom_specializations)
    chosen.update(k for keys in character.occupation_group_choices.values() for k in keys)
    occupation = next((o for o in ruleset.occupations if o.key == character.occupation), None)
    if occupation:
        chosen.update(occupation.fixed_skills)
    for allocations in (character.occupation_skills, character.interest_skills):
        chosen.update(k for k, v in allocations.items() if v.points)
    result = {}
    for key in sorted(chosen & definitions.keys()):
        skill = definitions[key]
        policy = ruleset.specialization_policies.get(skill.specialization_group)
        if not policy or not policy.requires_keeper_approval:
            continue
        if policy.custom_only and not key.startswith("custom_"):
            continue
        # Approval follows this exact specialty and era, never arbitrary imported flags.
        payload = [ruleset.id, ruleset.version, key, skill.display_name, character.era]
        token = hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()
        result[key] = (token, skill.display_name, policy.note)
    return result


def approve_specializations(character, ruleset, keys):
    requirements = review_requirements(character, ruleset)
    if len(keys) != len(set(keys)) or not set(keys) <= requirements.keys():
        raise ValueError("只能核准当前已选、需要 KP 引入的专业")
    character.specialization_approvals = {
        key: token
        for key, (token, _, _) in requirements.items()
        if key in keys or character.specialization_approvals.get(key) == token
    }


def validate_specialization_approvals(character, ruleset, definitions, issue):
    requirements = review_requirements(character, ruleset, definitions)
    character.specialization_approvals = {
        key: token
        for key, (token, _, _) in requirements.items()
        if character.specialization_approvals.get(key) == token
    }
    for key, (_, label, note) in requirements.items():
        if key not in character.specialization_approvals:
            issue(f"specialization_approvals.{key}", "keeper_approval", f"{label}：{note}")
