from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.domain.character_details import AssetDetail, Background, EquipmentEntry, Finances
from app.domain.mythos import InitialMythosSource, KnownSpell, MythosExperience
from app.domain.specializations import CustomSpecialization


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
    purpose: Literal[
        "attribute", "luck", "education_check", "education_gain", "experience_san", "initial_mythos"
    ] = "attribute"
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
    experience: int = 0


class ExperienceSelection(DomainModel):
    package: Literal["war", "police", "criminal", "medical", "mythos"]
    mythos: MythosExperience | None = None
    variant: str = Field(default="", max_length=32)
    history: str = Field(default="", max_length=2000)
    background_kind: Literal["scar", "phobia", "mania"] = "scar"
    background_detail: str = Field(default="", max_length=1000)
    choices: dict[str, list[str]] = Field(default_factory=dict, max_length=8)
    war_year: Annotated[StrictInt, Field(ge=1, le=9999)] | None = None
    scenario_year: Annotated[StrictInt, Field(ge=1, le=9999)] | None = None
    age_at_war: Annotated[StrictInt, Field(ge=1, le=150)] | None = None


class OccupationSkillReplacement(DomainModel):
    original_skill: str = Field(min_length=1, max_length=64)
    replacement_skill: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=2000)


class InitialMythos(DomainModel):
    source: Literal["occultist"] = "occultist"
    value: Annotated[StrictInt, Field(ge=1, le=99)]
    reason: str = Field(min_length=1, max_length=2000)


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
    occupation_attribute: str | None = None
    occupation_group_choices: dict[str, list[str]] = Field(default_factory=dict)
    selected_specializations: list[str] = Field(default_factory=list, max_length=200)
    custom_specializations: list[CustomSpecialization] = Field(default_factory=list, max_length=50)
    specialization_approvals: dict[str, str] = Field(default_factory=dict, max_length=200)
    occupation_skill_replacement: OccupationSkillReplacement | None = None
    initial_mythos_proposal: InitialMythos | None = None
    occupation_exception_approvals: dict[str, str] = Field(default_factory=dict, max_length=2)
    effective_occupation_skills: list[str] = Field(default_factory=list)
    initial_mythos: Annotated[StrictInt, Field(ge=0, le=99)] = 0
    initial_mythos_sources: list[InitialMythosSource] = Field(default_factory=list)
    initial_belief: Literal["believer", "unbeliever"] | None = None
    known_spells: list[KnownSpell] = Field(default_factory=list)
    experience: ExperienceSelection | None = None
    experience_skills: dict[str, SkillAllocation] = Field(default_factory=dict)
    experience_rolls: dict[str, RollRecord] = Field(default_factory=dict, max_length=5)
    experience_approvals: dict[str, str] = Field(default_factory=dict, max_length=1)
    experience_effects: dict = Field(default_factory=dict)
    era: Literal["1920s", "modern"] = "1920s"
    background: Background = Field(default_factory=Background)
    asset_details: list[AssetDetail] = Field(default_factory=list, max_length=100)
    finances: Finances = Field(default_factory=Finances)
    equipment: list[EquipmentEntry] = Field(default_factory=list, max_length=100)
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
