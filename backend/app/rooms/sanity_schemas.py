from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictInt, field_validator, model_validator

from app.domain.character import DomainModel
from app.rules.sanity import loss_bounds

Number = Annotated[StrictInt, Field(ge=0, le=1_000_000)]


class SanityEffect(DomainModel):
    id: str = Field(min_length=1, max_length=80)
    encounter: str = Field(min_length=1, max_length=200)
    trigger: Literal["action_target", "entity_revealed"] = "action_target"
    automation: Literal["automatic", "host_review"] = "host_review"
    repeat: Literal["first_only", "host_confirmed"] = "first_only"
    action_types: list[Literal["observe", "investigate", "interact", "use_item"]] = Field(
        default_factory=lambda: ["observe", "investigate", "interact"], max_length=4
    )
    condition: str = Field(default="", max_length=300)
    success_loss: str = Field(default="0", max_length=32)
    failure_loss: str = Field(max_length=32)
    source: str = Field(min_length=1, max_length=200)
    page: Annotated[StrictInt, Field(ge=1, le=2000)]
    basis: str = Field(min_length=1, max_length=500)
    visibility: Literal["public", "actor_and_host", "host_only"] = "actor_and_host"
    mythos: bool = False
    involuntary_action: str = Field(default="惊恐地呆立一瞬", min_length=1, max_length=200)

    @field_validator("success_loss", "failure_loss")
    @classmethod
    def formula(cls, value):
        loss_bounds(value)
        return value


class SanityState(DomainModel):
    day_start_san: Annotated[StrictInt, Field(ge=0, le=99)] | None = None
    day_loss: Number = 0
    mythos_gain: Annotated[StrictInt, Field(ge=0, le=99)] = 0
    mythos_insanity_count: Number = 0
    kind: Literal["none", "temporary", "indefinite", "permanent"] = "none"
    phase: Literal["none", "awaiting_symptom", "bout", "underlying"] = "none"
    started_minute: Number | None = None
    ends_minute: Number | None = None
    bout_end_minute: Number | None = None
    bout_end_round: Number | None = None
    symptom: str = Field(default="", max_length=500)
    trigger_id: str | None = None
    history: list[dict] = Field(default_factory=list)


class SanityRequest(DomainModel):
    target_member_id: UUID
    entity_id: str = Field(min_length=1, max_length=80)
    effect_id: str = Field(min_length=1, max_length=80)
    source_event_seq: Annotated[StrictInt, Field(ge=1)]


class HostSanityRequest(SanityRequest):
    encounter_confirmed: bool = False
    repeat_confirmed: bool = False
    reason: str = Field(default="", max_length=500)


class EncounterReview(DomainModel):
    source_event_seq: Annotated[StrictInt, Field(ge=1)]
    entity_id: str
    effect_id: str
    approve: bool
    target_member_ids: list[UUID] = Field(default_factory=list, max_length=30)
    repeat_confirmed: bool = False
    reason: str = Field(min_length=1, max_length=500)


class SanityRoll(DomainModel):
    # A stale click for SAN cannot accidentally roll the following loss/INT stage.
    expected_stage: Literal["san", "loss", "int", "duration"]


class SanityManagement(DomainModel):
    expected_revision: Annotated[StrictInt, Field(ge=1)]
    operation: Literal["advance", "new_day", "symptom", "end_bout", "recover"]
    reason: str = Field(min_length=1, max_length=500)
    slot_id: UUID | None = None
    minute: Number | None = None
    round: Number | None = None
    symptom: str = Field(default="", max_length=500)
    mode: Literal["realtime", "summary"] = "realtime"
    recovery_basis: Literal["elapsed", "safe_sleep", "host_treatment", "chapter_end"] = "elapsed"


class ResourceCorrection(DomainModel):
    expected_revision: Annotated[StrictInt, Field(ge=1)]
    slot_id: UUID
    resource: Literal["hp", "mp", "san", "luck"]
    value: Annotated[StrictInt, Field(ge=0, le=100_000)] | None
    reason: str = Field(min_length=1, max_length=500)


class SanityProgress(DomainModel):
    origin: Literal["automatic", "host_review", "host"] = "host"
    insanity_kind: str = "none"
    phase: str = "none"
    symptom: str = ""
    effect: SanityEffect
    source_event_seq: int
    entity_id: str
    stage: Literal["san", "loss", "int", "duration", "symptom", "done"] = "san"
    before: int
    after: int | None = None
    loss: int | None = None
    formula: str | None = None
    rolls: dict = Field(default_factory=dict)
    immune: bool = False

    @model_validator(mode="after")
    def validate_loss(self):
        if self.formula is not None:
            loss_bounds(self.formula)
        return self
