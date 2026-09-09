"""Strict, credential-free boundaries for the playable Agent runtime."""

from datetime import datetime
from typing import Annotated, Literal, TypedDict
from uuid import UUID, uuid4

from pydantic import Field, StrictInt, StringConstraints

from app.domain.character import DomainModel, utc_now
from app.rooms.schemas import Name, Visibility

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Role = Literal["keeper", "investigator"]
Difficulty = Literal["regular", "hard", "extreme"]
MemoryKind = Literal["observation", "belief", "goal", "relationship", "summary"]
MemoryScope = Literal["public", "agent_private", "keeper_only"]


class ProfileInput(DomainModel):
    role: Role
    name: Name
    background: str = Field(default="", max_length=1000)
    personality: str = Field(default="", max_length=1000)
    goals: str = Field(default="", max_length=1000)
    speaking_style: str = Field(default="", max_length=500)
    action_tendency: str = Field(default="", max_length=500)
    model_preset: Literal["default"] = "default"


class AgentProfile(ProfileInput):
    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class DraftRequest(DomainModel):
    role: Role
    concept: Text


class BindingInput(DomainModel):
    member_id: UUID
    profile_id: UUID


class AgentConfig(DomainModel):
    enabled: bool = True


class ModuleInput(DomainModel):
    module_id: str = Field(min_length=1, max_length=80)


class ActionInput(DomainModel):
    text: Text
    client_request_id: UUID
    actor_member_id: UUID | None = None


class Empty(DomainModel):
    pass


class CheckRequest(DomainModel):
    target_member_id: UUID
    kind: Literal["attribute", "skill"] = "skill"
    name: str = Field(min_length=1, max_length=80)
    difficulty: Difficulty = "regular"
    bonus_dice: Annotated[StrictInt, Field(ge=0, le=2)] = 0
    penalty_dice: Annotated[StrictInt, Field(ge=0, le=2)] = 0
    visibility: Visibility = "public"
    reason: Text
    clue_id: str | None = Field(default=None, max_length=80)


class PendingCheck(CheckRequest):
    id: UUID = Field(default_factory=uuid4)
    room_id: UUID
    slot_id: UUID
    value: Annotated[StrictInt, Field(ge=0, le=999)]
    requester: UUID
    agent_run_id: UUID
    status: Literal["pending", "resolved", "cancelled"] = "pending"
    dice: dict | None = None
    result: dict | None = None
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None


class CharacterArgs(DomainModel):
    member_id: UUID


class ClueArgs(DomainModel):
    clue_id: str = Field(min_length=1, max_length=80)


class SceneArgs(DomainModel):
    scene_id: str = Field(min_length=1, max_length=80)


class SpeechArgs(DomainModel):
    text: Text


class MemoryArgs(DomainModel):
    kind: Literal["observation", "belief", "goal", "relationship"]
    scope: MemoryScope
    content: Text
    source_event_ids: list[Annotated[StrictInt, Field(ge=1)]] = Field(
        default_factory=list, max_length=30
    )
    salience: Annotated[StrictInt, Field(ge=0, le=10)] = 5
    supersedes_id: UUID | None = None


class PrivateMemoryArgs(DomainModel):
    content: Text
    source_event_ids: list[Annotated[StrictInt, Field(ge=1)]] = Field(
        default_factory=list, max_length=30
    )
    salience: Annotated[StrictInt, Field(ge=0, le=10)] = 5
    supersedes_id: UUID | None = None


class PlannedTool(DomainModel):
    name: str = Field(min_length=1, max_length=80)
    arguments: dict = Field(default_factory=dict)


class AgentDecision(DomainModel):
    # This is an action plan, never hidden reasoning or a model-generated dice result.
    tools: list[PlannedTool] = Field(max_length=4)


class SummaryOutput(DomainModel):
    content: Text


class AgentCycleState(TypedDict):
    cycle_id: str
    room_id: str
    triggering_member_id: str
    triggering_event_seq: int
    current_node: str
    keeper_run_id: str | None
    pending_check_id: str | None
    tool_results: list[str]
    teammate_queue: list[str]
    completed_teammate_ids: list[str]
    call_count: int
    tool_count: int
    status: str
    safe_error: str | None
