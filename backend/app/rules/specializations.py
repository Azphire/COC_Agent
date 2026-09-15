"""Build per-character definitions without changing the shared skill catalogue."""

from app.domain.specializations import normalized_name


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
        definitions[custom.id] = template.model_copy(
            update={
                "key": custom.id,
                "display_name": template.display_name.split("（")[0] + f"（{custom.name}）",
            }
        )
    return definitions


def snapshot_skill_names(snapshot):
    labels = {"language": "外语", "art_craft": "艺术与手艺", "science": "科学"}
    return {
        s["id"]: f"{labels[s['group']]}（{s['name']}）"
        for s in snapshot.get("custom_specializations", [])
        if s["id"] in snapshot.get("skill_values", {}) and s["group"] in labels
    }
