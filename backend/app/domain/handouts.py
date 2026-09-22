"""Source-reviewed module handouts and their immutable character adjustments.

Kept independent from CharacterData so preparation, room and character schemas can
share the same strict definition without circular imports.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class HandoutModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


Points = Annotated[StrictInt, Field(ge=0, le=999)]


class HandoutAdjustments(HandoutModel):
    ruleset_id: str = Field(default="coc7-character-creation", max_length=64)
    required_age: Annotated[StrictInt, Field(ge=1, le=150)] | None = None
    required_occupation: str | None = Field(default=None, max_length=64)
    required_era: Literal["1920s", "modern"] | None = None
    requirements_note: str = Field(default="", max_length=1000)
    attribute_points: Points = 0
    attribute_choices: list[str] = Field(default_factory=list, max_length=8)
    attribute_maximum: Annotated[StrictInt, Field(ge=1, le=999)] | None = None
    attribute_maxima: dict[str, Annotated[StrictInt, Field(ge=1, le=999)]] = Field(
        default_factory=dict, max_length=8,
    )
    skill_bonuses: dict[str, Points] = Field(default_factory=dict, max_length=30)
    credit_maximum: Annotated[StrictInt, Field(ge=0, le=99)] | None = None
    skill_maximum: Annotated[StrictInt, Field(ge=1, le=999)] = 99
    order_note: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def valid_choices(self):
        if len(set(self.attribute_choices)) != len(self.attribute_choices):
            raise ValueError("HO 属性选项不能重复")
        if bool(self.attribute_points) != bool(self.attribute_choices):
            raise ValueError("HO 属性点和可分配属性必须一起配置")
        if not self.attribute_maxima.keys() <= set(self.attribute_choices):
            raise ValueError("HO 属性上限必须对应可分配属性")
        return self


class PreparedHandout(HandoutModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    title: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=30000)
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_pages: list[Annotated[StrictInt, Field(ge=1)]] = Field(min_length=1, max_length=30)
    source_block_ids: list[str] = Field(min_length=1, max_length=100)
    adjustments: HandoutAdjustments | None = None

    @model_validator(mode="after")
    def distinct_sources(self):
        if len(set(self.source_pages)) != len(self.source_pages):
            raise ValueError("HO 来源页码不能重复")
        if len(set(self.source_block_ids)) != len(self.source_block_ids):
            raise ValueError("HO 来源段落不能重复")
        return self


class ModuleHandoutSelection(HandoutModel):
    preparation_id: str = Field(min_length=1, max_length=80)
    handout_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    attribute_allocations: dict[str, Points] = Field(default_factory=dict, max_length=8)


class CharacterHandout(ModuleHandoutSelection):
    # This definition is resolved by the server, never accepted in a PATCH.
    definition: PreparedHandout

    @model_validator(mode="after")
    def same_handout(self):
        if self.definition.id != self.handout_id:
            raise ValueError("HO 选择与冻结来源定义不一致")
        return self


class HandoutEffect(HandoutModel):
    target: Literal["attribute", "skill"]
    key: str
    base_value: int
    adjustment: int
    final_value: int
    source_pages: list[int]
