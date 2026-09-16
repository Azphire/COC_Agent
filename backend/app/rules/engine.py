import math
from collections.abc import Mapping

from app.dice.service import parse_dice
from app.domain.character import CharacterData, PointBalances, ValidationIssue, ValidationResult
from app.rules.age import adjust_for_age, age_band, expected_rolls
from app.rules.schemas import CalculationRule, RuleSet
from app.rules.specializations import character_skills


def calculate(rule: CalculationRule, values: Mapping[str, float]) -> float | str:
    inputs = [values[key] for key in rule.input_keys()]
    match rule.operation:
        case "copy":
            value = inputs[0]
        case "multiply":
            value = inputs[0] * rule.factor
        case "sum":
            value = sum(inputs)
        case "average":
            value = sum(inputs) / len(inputs)
        case "table_lookup":
            value = next(
                row.value
                for row in rule.lookup_table
                if row.upper_bound is None or inputs[0] <= row.upper_bound
            )
        case "movement":
            strength, dexterity, size = inputs
            value = (
                9
                if strength > size and dexterity > size
                else (7 if strength < size and dexterity < size else 8)
            )
    if isinstance(value, str):
        return value
    match rule.rounding:
        case "floor":
            return math.floor(value)
        case "ceil":
            return math.ceil(value)
        case "nearest":
            # Ties round away from zero, independent of Python's bankers' rounding.
            return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)
        case "none":
            return value


def recalculate(character: CharacterData, ruleset: RuleSet) -> None:
    """Mutate only computed fields; invalid user allocations remain editable drafts."""
    issues: list[ValidationIssue] = []

    def issue(field: str, code: str, message: str) -> None:
        issues.append(ValidationIssue(field=field, code=code, message=message))

    definitions = character_skills(character, ruleset, issue)
    if not character.name.strip():
        issue("name", "required", "请填写角色姓名")
    values = {key: item.value for key, item in character.attributes.items()}
    known_attributes = {definition.key for definition in ruleset.attributes}
    for key in values.keys() - known_attributes:
        issue(f"attributes.{key}", "unknown", "规则集中不存在此属性")
    for definition in ruleset.attributes:
        value = values.get(definition.key)
        minimum = definition.minimum
        if character.creation_mode == "point_buy" and definition.point_buy_minimum is not None:
            minimum = definition.point_buy_minimum
        if value is None:
            issue(f"attributes.{definition.key}", "required", "属性不能为空")
        elif not minimum <= value <= definition.maximum:
            issue(
                f"attributes.{definition.key}",
                "range",
                f"{definition.display_name}须在 {minimum}–{definition.maximum} 之间",
            )

    raw_values = dict(values)
    issues.extend(adjust_for_age(character, ruleset))
    values = dict(character.effective_attributes)

    character.derived_values = {}
    for rule in ruleset.derived_values:
        if set(rule.input_keys()) <= values.keys():
            result = calculate(rule, values)
            character.derived_values[rule.key] = result
            if not isinstance(result, str):
                values[rule.key] = result

    if ruleset.age_rules:
        band = age_band(character, ruleset)
        if band:
            movement = ruleset.age_rules.movement_key
            if movement in character.derived_values:
                character.derived_values[movement] -= band.movement_penalty
            luck = [
                record.total * record.multiplier
                for record in character.roll_records
                if record.purpose == "luck"
            ]
            if luck:
                character.derived_values["luck"] = max(luck)
        for key, value in character.effective_attributes.items():
            character.derived_values[f"{key}_half"] = value // 2
            character.derived_values[f"{key}_fifth"] = value // 5

    points = ruleset.points
    assert points is not None

    def pool(rule: CalculationRule, field: str) -> int:
        if not set(rule.input_keys()) <= values.keys():
            return 0
        result = calculate(rule, values)
        if result < 0 or result != int(result):
            issue(field, "pool", "技能点总额必须是非负整数，请检查属性和规则配置")
            return 0
        return int(result)

    occupation = next((o for o in ruleset.occupations if o.key == character.occupation), None)
    occupation_pool = pool(points.occupation_skill_pool_rule, "occupation_skills")
    if occupation and occupation.point_formula:
        formula = occupation.point_formula
        occupation_pool = sum(values.get(k, 0) * v for k, v in formula.fixed.items())
        if formula.choice_attributes:
            if character.occupation_attribute not in formula.choice_attributes:
                issue("occupation_attribute", "required", "请选择职业点数使用的属性")
            else:
                occupation_pool += (
                    values.get(character.occupation_attribute, 0) * formula.choice_multiplier
                )
        elif character.occupation_attribute is not None:
            issue("occupation_attribute", "selection", "此职业没有可选点数属性")
    interest_pool = pool(points.interest_skill_pool_rule, "interest_skills")
    attribute_balance = None
    if character.creation_mode == "point_buy":
        spent = sum(
            (
                raw_values.get(item.key, item.minimum)
                - (item.minimum if points.attribute_cost_origin == "minimum" else 0)
            )
            * item.point_buy_cost
            for item in ruleset.attributes
        )
        attribute_balance = points.attribute_pool - spent
    character.remaining_points = PointBalances(
        attributes=attribute_balance,
        occupation=occupation_pool
        - sum(item.points for item in character.occupation_skills.values()),
        interest=interest_pool - sum(item.points for item in character.interest_skills.values()),
    )
    for field, remaining in character.remaining_points.model_dump().items():
        if remaining is not None:
            if remaining < 0:
                issue(f"remaining_points.{field}", "overspent", f"点数超支 {-remaining} 点")
            elif remaining > 0 and (
                not points.allow_unspent_points
                or (field == "attributes" and not points.allow_unspent_attribute_points)
            ):
                issue(f"remaining_points.{field}", "unspent", f"必须分配剩余 {remaining} 点")

    occupation = next(
        (item for item in ruleset.occupations if item.key == character.occupation), None
    )
    allowed = set()
    if occupation is None:
        issue("occupation", "required", "请选择规则集中存在的职业")
    else:
        if character.era not in occupation.eras:
            issue("occupation", "era", "此职业不适用于所选年代")
        selected = character.selected_occupation_skills
        if len(selected) != len(set(selected)):
            issue("selected_occupation_skills", "duplicate", "可选职业技能不能重复")
        if not occupation.point_formula and len(selected) != occupation.required_selection_count:
            issue(
                "selected_occupation_skills",
                "selection_count",
                f"请选择 {occupation.required_selection_count} 项可选职业技能",
            )
        if not occupation.point_formula and not set(selected) <= set(occupation.selectable_skills):
            issue("selected_occupation_skills", "selection", "所选技能不在此职业可选范围内")
        allowed = set(occupation.fixed_skills) | (set(selected) & set(occupation.selectable_skills))
        if occupation.credit_rating_minimum is not None:
            allowed.add("credit_rating")
        if occupation.skill_groups or occupation.point_formula:
            from app.rules.character_options import occupation_choices

            allowed = occupation_choices(
                character, ruleset, occupation, issue, definitions.values()
            ) | {"credit_rating"}
    from app.rules.occupation_exceptions import apply_initial_mythos, apply_occupation_options

    allowed = apply_occupation_options(character, ruleset, occupation, allowed, issue)
    selected = character.selected_specializations
    if len(selected) != len(set(selected)) or any(
        k not in definitions or not definitions[k].specialization_group for k in selected
    ):
        issue("selected_specializations", "selection", "专业必须唯一且来自目录或本卡自定义专业")
    active_specializations = set(selected) | allowed
    from app.rules.specializations import validate_specialization_approvals

    validate_specialization_approvals(character, ruleset, definitions, issue)
    for field in ("occupation_skills", "interest_skills"):
        for key, allocation in getattr(character, field).items():
            if key not in definitions:
                issue(f"{field}.{key}", "unknown", "规则集中不存在此技能")
            elif allocation.points and not definitions[key].allocatable:
                issue(f"{field}.{key}", "forbidden", "创建角色时不能给此技能分配点数")
            elif allocation.points and character.era not in definitions[key].eras:
                issue(f"{field}.{key}", "era", "该技能不适用于所选年代")
            elif (
                allocation.points
                and definitions[key].specialization_group
                and key not in active_specializations
            ):
                issue(f"{field}.{key}", "specialization", "请先选择此专业")
            if allocation.points < 0:
                issue(f"{field}.{key}", "negative", "技能点不能为负数")
            if field == "occupation_skills" and allocation.points and key not in allowed:
                issue(f"{field}.{key}", "occupation", "该技能未被选为职业技能")
    character.skill_values = {}
    character.skill_base_values = {}
    for key, definition in definitions.items():
        total = definition.base_value
        if definition.base_rule and set(definition.base_rule.input_keys()) <= values.keys():
            total = int(calculate(definition.base_rule, values))
        character.skill_base_values[key] = total
        for allocations in (character.occupation_skills, character.interest_skills):
            if key in allocations:
                total += allocations[key].points
        character.skill_values[key] = total
        if total > definition.maximum:
            for field in ("occupation_skills", "interest_skills"):
                issue(f"{field}.{key}", "maximum", f"技能合计不能超过 {definition.maximum}")
    if occupation and occupation.credit_rating_minimum is not None:
        credit = character.skill_values.get("credit_rating", 0)
        if not occupation.credit_rating_minimum <= credit <= occupation.credit_rating_maximum:
            issue(
                "occupation_skills.credit_rating",
                "credit_rating",
                f"职业要求信用评级 {occupation.credit_rating_minimum}–"
                f"{occupation.credit_rating_maximum}",
            )

    apply_initial_mythos(character)
    character.skill_half_values = {key: value // 2 for key, value in character.skill_values.items()}
    from app.rules.character_options import credit_finances

    character.finances = credit_finances(
        character.skill_values.get("credit_rating", 0), character.era
    )
    if (
        character.background.key_connection
        and not getattr(character.background, character.background.key_connection).strip()
    ):
        issue("background.key_connection", "required", "关键背景联系须指向已填写的背景条目")
    if (
        not character.finances.assets_lower_bound
        and sum(a.value for a in character.asset_details) > character.finances.assets
    ):
        issue("asset_details", "overspent", "资产明细合计超过信用档位的资产总额")
    from app.rules.equipment import validate_equipment

    validate_equipment(character, issue)
    character.skill_fifth_values = {
        key: value // 5 for key, value in character.skill_values.items()
    }

    specs = {
        key: (purpose, formula, multiplier)
        for key, purpose, formula, multiplier in expected_rolls(character, ruleset)
    }
    records = character.roll_records
    if len(records) != len(specs) or {record.attribute for record in records} != specs.keys():
        issue("roll_records", "incomplete", "必须有完整且不重复的属性、幸运和年龄掷骰记录")
    for record in records:
        try:
            dice = parse_dice(record.formula)
            consistent = (
                specs.get(record.attribute) == (record.purpose, record.formula, record.multiplier)
                and len(record.dice) == dice.count
                and all(1 <= value <= dice.sides for value in record.dice)
                and record.modifier == dice.modifier
                and record.total == sum(record.dice) + record.modifier
            )
            if record.purpose == "attribute":
                consistent = consistent and (
                    record.total * record.multiplier == raw_values.get(record.attribute)
                )
        except ValueError:
            consistent = False
        if not consistent:
            issue("roll_records", "inconsistent", "掷骰记录与骰式、倍率或属性不一致")
    character.validation = ValidationResult(valid=not issues, issues=issues)
