"""Creation proposals and immutable, locally approved knowledge records."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt


class MythosModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MythosBackground(MythosModel):
    kind: Literal["scar", "phobia", "mania", "encounter"]
    detail: str = Field(max_length=1000)


class SpellCandidate(MythosModel):
    id: UUID
    name: str = Field(min_length=1, max_length=120)
    source: str = Field(min_length=1, max_length=500)
    acquisition: Literal["experience_mythos"] = "experience_mythos"


class KnownSpell(SpellCandidate):
    permission: str
    casting_status: Literal["unsupported"] = "unsupported"


class MythosExperience(MythosModel):
    knowledge: Literal["reading", "direct"] = "reading"
    belief: Literal["believer", "unbeliever"] = "believer"
    method: Literal["manual", "suggested_roll"] = "manual"
    value: Annotated[StrictInt, Field(ge=0, le=99)] | None = None
    backgrounds: list[MythosBackground] = Field(default_factory=list, max_length=2)
    spells: list[SpellCandidate] = Field(default_factory=list, max_length=50)


class InitialMythosSource(MythosModel):
    source: Literal["occupation:occultist", "experience:mythos"]
    value: Annotated[StrictInt, Field(ge=0, le=99)]
    approved: bool
    san_cost: Annotated[StrictInt, Field(ge=0, le=99)] = 0
