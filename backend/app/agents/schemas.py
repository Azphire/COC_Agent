"""Strict, credential-free boundaries for the playable Agent runtime."""

from datetime import datetime
from typing import Annotated, Literal, TypedDict
from uuid import UUID, uuid4

from pydantic import Field, StrictInt, StringConstraints

from app.domain.character import DomainModel, utc_now
from app.knowledge.schemas import GroundedClaim
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
    target_entity_id: str | None = Field(default=None, max_length=100)
    clarification_event_seq: int | None = Field(default=None, ge=1)


class ClarificationInput(ActionInput):
    clarification_event_seq: int = Field(ge=1)


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
    display_name: str = ""
    ruleset_id: str = "coc7-character-creation"
    policy_fingerprint: str = ""
    policy_target_id: str | None = None
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
    claims: list[GroundedClaim] = Field(default_factory=list, max_length=5)
    needs_host_ruling: bool = False


class SummaryOutput(DomainModel):
    content: Text


class CycleStage(DomainModel):
    schema_version: Literal[1] = 1
    status: Literal[
        "running", "waiting_for_roll", "waiting_for_review", "completed", "failed", "cancelled"
    ]
    safe_error: str | None = None
    error_category: str | None = None


class AgentCycleState(TypedDict):
    schema_version: int
    requires_clarification: bool
    clarification_question: str | None
    clarification_event_seq: int | None
    summary_attempted: bool
    stage_states: dict
    current_scene_node_id: str
    selected_node_ids: list[str]
    selected_block_ids: list[str]
    structure_snapshot_id: str
    module_source_hash: str
    module_fallback_mode: str
    navigation_revision: int
    transition_request: dict | None
    transition_result: dict | None
    cycle_id: str
    room_id: str
    triggering_member_id: str
    triggering_event_seq: int
    current_node: str
    keeper_run_id: str | None
    pending_check_id: str | None
    wait_reason: str | None
    pending_review_id: str | None
    approved_entity_ids_used: list[str]
    proposed_entity_ids: list[str]
    revealed_entity_ids: list[str]
    scene_transition: str | None
    review_count: int
    review_result: dict | None
    deferred_tools: list[dict]
    rejection_rewrite_called: bool
    tool_results: list[str]
    teammate_queue: list[str]
    completed_teammate_ids: list[str]
    call_count: int
    tool_count: int
    status: str
    safe_error: str | None
