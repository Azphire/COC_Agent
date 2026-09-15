"""Source-backed occupation groups and credit brackets; all results are computed."""

from app.domain.character_details import Finances


def group_options(group, ruleset, era, skills=None):
    return {
        s.key
        for s in (ruleset.skills if skills is None else skills)
        if s.allocatable
        and s.key != "credit_rating"
        and era in s.eras
        and (
            group.any_skill
            or s.key in group.skills
            or s.specialization_group in group.specialization_groups
        )
    }


def occupation_choices(character, ruleset, occupation, issue, skills=None):
    skills = ruleset.skills if skills is None else skills
    if (
        character.selected_occupation_skills
        and not character.occupation_group_choices
        and occupation.legacy_selection_group
    ):
        character.occupation_group_choices = {
            **occupation.legacy_group_defaults,
            occupation.legacy_selection_group: list(character.selected_occupation_skills),
        }
        character.selected_occupation_skills = []
    allowed = set(occupation.fixed_skills)
    groups = {g.key: g for g in occupation.skill_groups}
    if set(character.occupation_group_choices) - groups.keys():
        issue("occupation_group_choices", "unknown", "当前职业不存在这些技能分组")
    if character.selected_occupation_skills:
        issue("selected_occupation_skills", "selection", "请按当前职业分组选择技能")
    for group in occupation.skill_groups:
        selected = character.occupation_group_choices.get(group.key, [])
        field = f"occupation_group_choices.{group.key}"
        if len(selected) != group.count:
            issue(field, "selection_count", f"{group.display_name}须选择 {group.count} 项")
        if len(selected) != len(set(selected)) or set(selected) & allowed:
            issue(field, "duplicate", "职业技能不得在固定项或不同分组重复计数")
        options = group_options(group, ruleset, character.era, skills)
        if not set(selected) <= options:
            issue(field, "selection", "所选技能不符合本组或年代要求")
        # Mixed direction groups count "fighting" or "language" only once.
        directions = [
            next(s.specialization_group for s in skills if s.key == k)
            for k in selected
            if k in options and k not in group.skills
        ]
        if not group.any_skill and len(directions) != len(set(directions)):
            issue(field, "duplicate_direction", "本组选项须来自不同技能方向")
        allowed |= set(selected) & options
    return allowed


def credit_finances(credit, era):
    # 1907 PDF37 / printed36: dollar figures, not an exchange-rate conversion.
    if credit <= 0:
        return Finances(
            level="身无分文",
            cash=0.5 if era == "1920s" else 10,
            spending=0.5 if era == "1920s" else 10,
        )
    if credit >= 99:
        return Finances(
            level="富豪",
            cash=50000 if era == "1920s" else 1000000,
            assets=5000000 if era == "1920s" else 100000000,
            spending=5000 if era == "1920s" else 100000,
            assets_lower_bound=True,
        )
    for upper, level, cash, assets, spending in [
        (9, "贫穷", 1, 10, 2),
        (49, "标准", 2, 50, 10),
        (89, "小康", 5, 500, 50),
        (98, "富裕", 20, 2000, 250),
    ]:
        if credit <= upper:
            factor = 1 if era == "1920s" else 20
            return Finances(
                level=level,
                cash=credit * cash * factor,
                assets=credit * assets * factor,
                spending=spending * factor,
            )
