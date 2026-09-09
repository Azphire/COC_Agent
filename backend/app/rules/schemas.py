from typing import Annotated, Literal, Self

from pydantic import Field, StrictInt, model_validator

from app.dice.service import parse_dice
from app.domain.character import DomainModel

Key = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]


class LookupRow(DomainModel):
    upper_bound: float | None
    value: float | str


class CalculationRule(DomainModel):
    operation: Literal["copy", "multiply", "sum", "average", "table_lookup", "movement"]
    source: Key | None = None
    sources: list[Key] = Field(default_factory=list, max_length=100)
    factor: float | None = Field(default=None, ge=-100_000, le=100_000)
    rounding: Literal["none", "floor", "ceil", "nearest"] = "none"
    lookup_table: list[LookupRow] = Field(default_factory=list, max_length=100)

    def input_keys(self) -> list[str]:
        return [self.source] if self.source is not None else self.sources

    @model_validator(mode="after")
    def check_operation(self) -> Self:
        single = self.operation in {"copy", "multiply", "table_lookup"}
        if single and (self.source is None or self.sources):
            raise ValueError("单源运算必须指定 source，不能指定 sources")
        if not single and (self.source is not None or not self.sources):
            raise ValueError("多源运算必须指定非空 sources，不能指定 source")
        if self.operation == "movement" and len(self.sources) != 3:
            raise ValueError("movement 按顺序需要力量、敏捷、体型三个来源")
        if (self.operation == "multiply") != (self.factor is not None):
            raise ValueError("仅 multiply 必须指定 factor")
        if self.operation == "table_lookup":
            if not self.lookup_table or self.lookup_table[-1].upper_bound is not None:
                raise ValueError("查表必须以 upper_bound: null 覆盖剩余范围")
            bounds = [row.upper_bound for row in self.lookup_table[:-1]]
            if any(value is None for value in bounds) or bounds != sorted(set(bounds)):
                raise ValueError("查表上界必须严格递增且不重叠")
        elif self.lookup_table:
            raise ValueError("仅 table_lookup 可以指定 lookup_table")
        if any(isinstance(row.value, str) for row in self.lookup_table):
            if self.rounding != "none":
                raise ValueError("文本查表结果不能舍入")
        return self


class AttributeDefinition(DomainModel):
    key: Key
    display_name: str
    minimum: Annotated[StrictInt, Field(ge=0, le=10_000)]
    maximum: Annotated[StrictInt, Field(ge=0, le=100_000)]
    random_formula: str = Field(max_length=32)
    point_buy_cost: Annotated[StrictInt, Field(gt=0, le=10_000)]
    random_multiplier: Annotated[StrictInt, Field(ge=1, le=100)] = 1
    point_buy_minimum: Annotated[StrictInt, Field(ge=0)] | None = None

    @model_validator(mode="after")
    def check_range(self) -> Self:
        dice = parse_dice(self.random_formula)
        if self.minimum > self.maximum:
            raise ValueError("属性下限不能大于上限")
        if (dice.count + dice.modifier) * self.random_multiplier < self.minimum or (
            dice.count * dice.sides + dice.modifier
        ) * self.random_multiplier > self.maximum:
            raise ValueError("骰式可能生成超出属性范围的结果")
        if self.point_buy_minimum is not None and self.point_buy_minimum > self.maximum:
            raise ValueError("购点属性下限不能大于上限")
        return self


class DerivedDefinition(CalculationRule):
    key: Key
    display_name: str
    visible: bool = True


class PointConfiguration(DomainModel):
    attribute_pool: Annotated[StrictInt, Field(ge=0, le=1_000_000)]
    occupation_skill_pool_rule: CalculationRule
    interest_skill_pool_rule: CalculationRule
    allow_unspent_points: bool
    attribute_cost_origin: Literal["minimum", "zero"] = "minimum"
    allow_unspent_attribute_points: bool = True


class SkillDefinition(DomainModel):
    key: Key
    display_name: str
    base_value: Annotated[StrictInt, Field(ge=0, le=100_000)]
    maximum: Annotated[StrictInt, Field(ge=1, le=100_000)]
    category: str
    base_rule: CalculationRule | None = None
    allocatable: bool = True

    @model_validator(mode="after")
    def check_base(self) -> Self:
        if self.base_value > self.maximum:
            raise ValueError("技能基础值不能超过上限")
        return self


class OccupationDefinition(DomainModel):
    key: Key
    display_name: str
    fixed_skills: list[Key] = Field(default_factory=list)
    selectable_skills: list[Key] = Field(default_factory=list)
    required_selection_count: Annotated[StrictInt, Field(ge=0)] = 0
    credit_rating_minimum: Annotated[StrictInt, Field(ge=0, le=99)] | None = None
    credit_rating_maximum: Annotated[StrictInt, Field(ge=0, le=99)] | None = None

    @model_validator(mode="after")
    def check_selection(self) -> Self:
        combined = self.fixed_skills + self.selectable_skills
        if len(set(combined)) != len(combined):
            raise ValueError("职业技能不能重复或同时固定和可选")
        if self.required_selection_count > len(self.selectable_skills):
            raise ValueError("可选技能不足")
        if (self.credit_rating_minimum is None) != (self.credit_rating_maximum is None):
            raise ValueError("信用评级上下限必须同时指定")
        if (
            self.credit_rating_minimum is not None
            and self.credit_rating_minimum > self.credit_rating_maximum
        ):
            raise ValueError("信用评级范围无效")
        return self


class AgeBand(DomainModel):
    minimum: Annotated[StrictInt, Field(ge=1)]
    maximum: Annotated[StrictInt, Field(le=150)]
    education_checks: Annotated[StrictInt, Field(ge=0, le=10)] = 0
    luck_rolls: Annotated[StrictInt, Field(ge=1, le=2)] = 1
    flat_adjustments: dict[Key, StrictInt] = Field(default_factory=dict)
    deduction_pool: Annotated[StrictInt, Field(ge=0)] = 0
    deduction_attributes: list[Key] = Field(default_factory=list)
    movement_penalty: Annotated[StrictInt, Field(ge=0)] = 0


class AgeConfiguration(DomainModel):
    education_attribute: Key
    education_cap: Annotated[StrictInt, Field(ge=1)]
    check_formula: str
    gain_formula: str
    luck_formula: str
    luck_multiplier: Annotated[StrictInt, Field(ge=1)]
    movement_key: Key
    bands: list[AgeBand] = Field(min_length=1)

    @model_validator(mode="after")
    def check_bands(self):
        for formula in (self.check_formula, self.gain_formula, self.luck_formula):
            parse_dice(formula)
        for index, band in enumerate(self.bands):
            if band.minimum > band.maximum:
                raise ValueError("年龄范围无效")
            if index and band.minimum != self.bands[index - 1].maximum + 1:
                raise ValueError("年龄分段必须连续且不重叠")
        return self


class SourceReference(DomainModel):
    filename: str
    section: str
    notes: str


class RuleSet(DomainModel):
    id: Key
    version: str = Field(min_length=1, max_length=32)
    display_name: str
    edition: str
    enabled: bool
    verification_status: Literal["verified", "unverified", "missing_source"]
    source_reference: list[SourceReference] | None = None
    notice: str
    reroll_policy: Literal["none"] = "none"
    attributes: list[AttributeDefinition] = Field(default_factory=list, max_length=100)
    derived_values: list[DerivedDefinition] = Field(default_factory=list, max_length=100)
    points: PointConfiguration | None = None
    skills: list[SkillDefinition] = Field(default_factory=list, max_length=100)
    occupations: list[OccupationDefinition] = Field(default_factory=list, max_length=100)
    age_rules: AgeConfiguration | None = None

    @model_validator(mode="after")
    def check_references(self) -> Self:
        if self.enabled and self.verification_status == "missing_source":
            raise ValueError("缺少来源的规则集不能启用")
        if self.verification_status == "verified" and not self.source_reference:
            raise ValueError("已核对的规则集必须记录来源")
        if self.edition != "development" and self.enabled:
            if self.verification_status != "verified" or not self.source_reference:
                raise ValueError("正式规则必须先核对来源")
        for items in (self.attributes, self.derived_values, self.skills, self.occupations):
            if len({item.key for item in items}) != len(items):
                raise ValueError("规则定义 key 不能重复")
        if self.enabled and not (
            self.attributes and self.points and self.skills and self.occupations
        ):
            raise ValueError("已启用规则集缺少车卡配置")
        known = {item.key for item in self.attributes}
        numeric = set(known)
        for rule in self.derived_values:
            if rule.key in known or not set(rule.input_keys()) <= numeric:
                raise ValueError("派生值引用不存在、重复或尚未定义（不允许循环）")
            known.add(rule.key)
            if not any(isinstance(row.value, str) for row in rule.lookup_table):
                numeric.add(rule.key)
        if self.points:
            for rule in (
                self.points.occupation_skill_pool_rule,
                self.points.interest_skill_pool_rule,
            ):
                if not set(rule.input_keys()) <= numeric:
                    raise ValueError("技能点公式引用不存在")
                if any(isinstance(row.value, str) for row in rule.lookup_table):
                    raise ValueError("技能点不能使用文本查表结果")
        skills = {item.key for item in self.skills}
        for occupation in self.occupations:
            if not set(occupation.fixed_skills + occupation.selectable_skills) <= skills:
                raise ValueError("职业引用了不存在的技能")
            if occupation.credit_rating_minimum is not None and "credit_rating" not in skills:
                raise ValueError("职业的信用评级缺少技能定义")
        for skill in self.skills:
            if skill.base_rule and not set(skill.base_rule.input_keys()) <= numeric:
                raise ValueError("技能基础值公式引用不存在")
            if skill.base_rule and any(
                isinstance(row.value, str) for row in skill.base_rule.lookup_table
            ):
                raise ValueError("技能基础值必须为数值")
        if self.age_rules:
            attributes = {item.key for item in self.attributes}
            if self.age_rules.education_attribute not in attributes:
                raise ValueError("年龄配置的教育属性不存在")
            if self.age_rules.movement_key not in numeric:
                raise ValueError("年龄配置的移动派生值不存在")
            for band in self.age_rules.bands:
                if not (set(band.flat_adjustments) | set(band.deduction_attributes)) <= attributes:
                    raise ValueError("年龄调整引用了不存在的属性")
        return self
