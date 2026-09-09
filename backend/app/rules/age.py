from app.domain.character import CharacterData, ValidationIssue
from app.rules.schemas import AgeBand, RuleSet


def age_band(character: CharacterData, ruleset: RuleSet) -> AgeBand | None:
    if not ruleset.age_rules or character.age is None:
        return None
    return next(
        (band for band in ruleset.age_rules.bands if band.minimum <= character.age <= band.maximum),
        None,
    )


def expected_rolls(character: CharacterData, ruleset: RuleSet) -> list[tuple[str, str, str, int]]:
    specs = []
    if character.creation_mode == "random":
        specs.extend(
            (item.key, "attribute", item.random_formula, item.random_multiplier)
            for item in ruleset.attributes
        )
    config, band = ruleset.age_rules, age_band(character, ruleset)
    if config and band:
        specs.extend(
            (f"luck_{index + 1}", "luck", config.luck_formula, config.luck_multiplier)
            for index in range(band.luck_rolls)
        )
        for index in range(band.education_checks):
            specs.append(
                (f"education_check_{index + 1}", "education_check", config.check_formula, 1)
            )
            specs.append((f"education_gain_{index + 1}", "education_gain", config.gain_formula, 1))
    return specs


def adjust_for_age(character: CharacterData, ruleset: RuleSet) -> list[ValidationIssue]:
    issues = []
    character.effective_attributes = {
        key: value.value for key, value in character.attributes.items()
    }

    def issue(field: str, message: str):
        issues.append(ValidationIssue(field=field, code="age", message=message))

    config, band = ruleset.age_rules, age_band(character, ruleset)
    if not config:
        if character.age_deductions:
            issue("age_deductions", "此规则集不使用年龄属性扣减")
        return issues
    if band is None:
        issue("age", "请选择已配置的年龄范围（第七版当前支持 15–89 岁）")
        return issues
    deductions = character.age_deductions
    if (
        not set(deductions) <= set(band.deduction_attributes)
        or any(value < 0 for value in deductions.values())
        or sum(deductions.values()) != band.deduction_pool
    ):
        issue("age_deductions", f"请在指定属性中分配合计 {band.deduction_pool} 点年龄扣减")
    for key, value in deductions.items():
        if key in band.deduction_attributes and key in character.effective_attributes:
            character.effective_attributes[key] -= value
    for key, value in band.flat_adjustments.items():
        if key in character.effective_attributes:
            character.effective_attributes[key] += value
    records = {record.attribute: record for record in character.roll_records}
    education = character.effective_attributes.get(config.education_attribute)
    if education is not None:
        for index in range(band.education_checks):
            check = records.get(f"education_check_{index + 1}")
            gain = records.get(f"education_gain_{index + 1}")
            if check and gain and check.total > education:
                education = min(config.education_cap, education + gain.total)
        character.effective_attributes[config.education_attribute] = education
    for key, value in character.effective_attributes.items():
        if value < 1:
            issue(f"attributes.{key}", "年龄调整后属性必须大于 0，请重新分配扣减")
    return issues
