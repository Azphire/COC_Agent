from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.agents.schemas import ProfileInput
from app.domain.character import DomainModel


class BatchInput(DomainModel):
    request_id: UUID
    count: int = Field(ge=1, le=6)
    preparation_id: str | None = Field(default=None, max_length=80)
    ruleset_id: str = "coc7-character-creation"
    era: Literal["1920s", "modern"] = "1920s"
    seed: str | None = Field(default=None, max_length=128)
    handout_ids: list[str | None] | None = Field(default=None, max_length=6)

    @model_validator(mode="after")
    def handout_count(self):
        if self.handout_ids is not None:
            if len(self.handout_ids) != self.count:
                raise ValueError("HO 数量必须与生成成员数一致")
            selected = [item for item in self.handout_ids if item]
            if len(set(selected)) != len(selected):
                raise ValueError("同一 HO 不能分给多个成员")
            if selected and not self.preparation_id:
                raise ValueError("HO 必须来自选定准备版本")
        return self


class OperationInput(DomainModel):
    request_id: UUID


class RerollInput(OperationInput):
    member_index: int | None = Field(default=None, ge=0, le=5)


class AdoptInput(OperationInput):
    approve_handouts: bool = False


class MemberEdit(OperationInput):
    character: dict | None = None
    profile: ProfileInput | None = None


class Persona(DomainModel):
    """The model can write prose only; numeric card fields are absent."""

    name: str = Field(min_length=1, max_length=80)
    background: str = Field(min_length=1, max_length=1000)
    personality: str = Field(min_length=1, max_length=1000)
    goals: str = Field(min_length=1, max_length=1000)
    speaking_style: str = Field(min_length=1, max_length=500)
    action_tendency: str = Field(min_length=1, max_length=500)
