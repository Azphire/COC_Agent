from typing import Annotated

from pydantic import Field, StrictInt, model_validator

from app.domain.character import BoundedInt, CharacteristicValue, DomainModel, SkillAllocation


class CreateCharacterRequest(DomainModel):
    ruleset_id: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=120)
    player_name: str | None = Field(default=None, max_length=120)
    age: Annotated[StrictInt, Field(ge=1, le=150)] | None = None
    derived_values: dict[str, int | float | str] | None = None


class PointBuyRequest(CreateCharacterRequest):
    attributes: dict[str, CharacteristicValue] | None = None


class PatchCharacterRequest(DomainModel):
    version: Annotated[StrictInt, Field(ge=1)]
    name: str | None = Field(default=None, max_length=120)
    player_name: str | None = Field(default=None, max_length=120)
    age: Annotated[StrictInt, Field(ge=1, le=150)] | None = None
    occupation: str | None = Field(default=None, max_length=64)
    selected_occupation_skills: list[str] | None = Field(default=None, max_length=100)
    attributes: dict[str, CharacteristicValue] | None = None
    occupation_skills: dict[str, SkillAllocation] | None = None
    interest_skills: dict[str, SkillAllocation] | None = None
    derived_values: dict[str, int | float | str] | None = None
    age_deductions: dict[str, BoundedInt] | None = None

    @model_validator(mode="after")
    def forbid_null_collections(self):
        for field in (
            "name",
            "selected_occupation_skills",
            "attributes",
            "occupation_skills",
            "interest_skills",
            "age_deductions",
        ):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} 不能为 null")
        return self


class FinalizeRequest(DomainModel):
    version: Annotated[StrictInt, Field(ge=1)]
