"""Player decisions and host approvals; never exposed as model tools."""

from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, StringConstraints, model_validator

from app.domain.character import DomainModel

Explanation = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class CheckChoice(DomainModel):
    operation: Literal["accept", "luck", "push"]
    spend: Annotated[StrictInt, Field(ge=1, le=99)] | None = None
    effort: str = Field(default="", max_length=500)


class PushConsequence(DomainModel):
    kind: Literal["condition", "time", "host_manual"]
    description: Explanation
    condition: str = Field(default="", max_length=120)
    minutes: Annotated[StrictInt, Field(ge=1, le=1440)] | None = None

    @model_validator(mode="after")
    def complete(self):
        self.condition = self.condition.strip()
        if self.kind == "condition" and not self.condition.strip():
            raise ValueError("请选择明确的角色状态")
        if self.kind == "time" and self.minutes is None:
            raise ValueError("请指定流逝的游戏分钟")
        return self


class PushReview(DomainModel):
    approve: StrictBool
    reason: Explanation
    consequence: PushConsequence | None = None


class ConsequenceHandled(DomainModel):
    reason: Explanation


class CheckRules(DomainModel):
    luck_spending: StrictBool
    expected_revision: Annotated[StrictInt, Field(ge=1)]
