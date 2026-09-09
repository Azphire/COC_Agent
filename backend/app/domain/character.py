from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictInt


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


BoundedInt = Annotated[StrictInt, Field(ge=-100_000, le=100_000)]


class CharacteristicValue(DomainModel):
    value: Annotated[StrictInt, Field(ge=-100_000, le=100_000)]


class SkillAllocation(DomainModel):
    points: Annotated[StrictInt, Field(ge=-100_000, le=100_000)]


class RollRecord(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    attribute: str = Field(min_length=1, max_length=64)
    formula: str = Field(max_length=32)
    dice: list[Annotated[StrictInt, Field(ge=1, le=1000)]] = Field(min_length=1, max_length=100)
    modifier: Annotated[StrictInt, Field(ge=-10_000, le=10_000)]
    total: Annotated[StrictInt, Field(ge=-10_000, le=110_000)]
    rolled_at: datetime = Field(default_factory=utc_now)
    source: Literal["system", "imported"] = "system"
    purpose: Literal["attribute", "luck", "education_check", "education_gain"] = "attribute"
    multiplier: Annotated[StrictInt, Field(ge=1, le=100)] = 1


class ValidationIssue(DomainModel):
    field: str
    code: str
    message: str


class ValidationResult(DomainModel):
    valid: bool = False
    issues: list[ValidationIssue] = Field(default_factory=list)


class PointBalances(DomainModel):
    attributes: int | None = None
    occupation: int = 0
    interest: int = 0


class CharacterData(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    ruleset_id: str
    ruleset_version: str
    creation_mode: Literal["random", "point_buy"]
    name: str = Field(default="", max_length=120)
    player_name: str | None = Field(default=None, max_length=120)
    age: Annotated[StrictInt, Field(ge=1, le=150)] | None = None
    occupation: str | None = None
    selected_occupation_skills: list[str] = Field(default_factory=list, max_length=100)
    attributes: dict[str, CharacteristicValue] = Field(default_factory=dict)
    effective_attributes: dict[str, int] = Field(default_factory=dict)
    age_deductions: dict[str, BoundedInt] = Field(default_factory=dict)
    derived_values: dict[str, int | float | str] = Field(default_factory=dict)
    occupation_skills: dict[str, SkillAllocation] = Field(default_factory=dict)
    interest_skills: dict[str, SkillAllocation] = Field(default_factory=dict)
    skill_values: dict[str, int] = Field(default_factory=dict)
    skill_base_values: dict[str, int] = Field(default_factory=dict)
    skill_half_values: dict[str, int] = Field(default_factory=dict)
    skill_fifth_values: dict[str, int] = Field(default_factory=dict)
    roll_records: list[RollRecord] = Field(default_factory=list, max_length=100)
    validation: ValidationResult = Field(default_factory=ValidationResult)
    remaining_points: PointBalances = Field(default_factory=PointBalances)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    version: Annotated[StrictInt, Field(ge=1)] = 1
    original_id: UUID | None = None


class CharacterDraft(CharacterData):
    status: Literal["draft"] = "draft"


class CharacterSheet(CharacterData):
    status: Literal["finalized"] = "finalized"


Character = Annotated[CharacterDraft | CharacterSheet, Field(discriminator="status")]


class ExportRuleset(DomainModel):
    id: str
    version: str
    verification_status: Literal["unverified", "verified", "missing_source"]


class CharacterExport(DomainModel):
    schema_version: Annotated[StrictInt, Field(ge=1, le=1)] = 1
    exported_at: datetime = Field(default_factory=utc_now)
    character: Character
    ruleset: ExportRuleset
