"""Versioned action boundaries; no model reasoning or world-state copies."""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.agents.schemas import CheckRequest, PlannedTool
from app.domain.character import DomainModel, utc_now
from app.knowledge.schemas import GroundedClaim
from app.module_ir.schemas import SceneTransition
from app.preparation.schemas import RevealConditions

IntentType = Literal[
    "observe",
    "investigate",
    "converse",
    "move",
    "interact",
    "use_item",
    "assist",
    "wait",
    "out_of_character",
    "unknown",
]
ErrorCategory = Literal[
    "invalid_arguments",
    "permission_denied",
    "precondition_failed",
    "context_missing",
    "entity_not_found",
    "revision_conflict",
    "already_applied",
    "model_schema_error",
    "model_timeout",
    "internal_error",
]


class PlayerIntent(DomainModel):
    schema_version: Literal[1] = 1
    type: IntentType
    actor_member_id: str
    actor_character_slot_id: str
    target_kind: (
        Literal["scene", "exit", "npc", "location", "clue", "item", "member", "node"] | None
    ) = None
    target_id: str | None = Field(default=None, max_length=100)
    target_text: str | None = Field(default=None, max_length=160)
    evidence_quote: str = Field(min_length=1, max_length=2000)
    requested_outcome: str = Field(default="", max_length=300)
    confidence: float = Field(ge=0, le=1)
    ambiguity_reason: str | None = Field(default=None, max_length=300)
    requires_clarification: bool = False
    clarification_question: str | None = Field(default=None, max_length=300)


class KeeperPlan(DomainModel):
    schema_version: Literal[1] = 1
    plan_id: str = Field(min_length=1, max_length=100)
    cycle_id: str
    parsed_intent: PlayerIntent
    current_scene_id: str
    target_entity_ids: list[str] = Field(default_factory=list, max_length=8)
    target_node_ids: list[str] = Field(default_factory=list, max_length=8)
    required_context: list[str] = Field(default_factory=list, max_length=8)
    proposed_tool_calls: list[PlannedTool] = Field(default_factory=list, max_length=4)
    proposed_check: CheckRequest | None = Field(
        default=None, json_schema_extra={"x-explicit-output": True}
    )
    proposed_reveal_entity_ids: list[str] = Field(default_factory=list, max_length=4)
    proposed_transition_id: str | None = Field(
        default=None, json_schema_extra={"x-explicit-output": True}
    )
    expected_next_phase: Literal["narration", "check", "host_review", "clarification"] = "narration"
    needs_clarification: bool = False
    needs_host_review: bool = False
    rationale_summary: str = Field(default="", max_length=300)
    source_node_ids: list[str] = Field(default_factory=list, max_length=8)
    source_evidence_ids: list[str] = Field(default_factory=list, max_length=8)
    source_entity_ids: list[str] = Field(default_factory=list, max_length=8)
    expected_navigation_revision: int = Field(default=0, ge=0)


class ActionRejection(DomainModel):
    index: int
    tool: str
    code: ErrorCategory
    reason: str = Field(max_length=400)


class ValidatedAction(DomainModel):
    index: int
    tool: PlannedTool
    phase: Literal["read", "proposal", "state"]


class ValidatedActionPlan(DomainModel):
    schema_version: Literal[1] = 1
    plan_id: str
    status: Literal[
        "approved",
        "partially_approved",
        "clarification_required",
        "host_review_required",
        "rejected",
    ]
    approved_actions: list[ValidatedAction] = Field(default_factory=list)
    rejected_actions: list[ActionRejection] = Field(default_factory=list)
    validation_reasons: list[str] = Field(default_factory=list)
    required_interrupt: Literal["human_roll", "host_review"] | None = None
    expected_navigation_revision: int = 0
    clarification_question: str | None = None


class NPCSpeech(DomainModel):
    entity_id: str
    text: str = Field(min_length=1, max_length=700)


class KeeperNarration(DomainModel):
    schema_version: Literal[1] = 1
    public_narration: str = Field(default="", max_length=2000)
    npc_speech: NPCSpeech | None = None
    grounded_claims: list[GroundedClaim] = Field(default_factory=list, max_length=5)
    public_entity_references: list[str] = Field(default_factory=list, max_length=8)
    check_result_reference: str | None = None
    transition_result_reference: str | None = None
    needs_host_ruling: bool = False


class ContextGap(DomainModel):
    schema_version: Literal[1] = 1
    missing_kind: Literal["node", "entity", "npc", "transition", "evidence"]
    requested_target: str
    current_scene: str
    attempted_tool: str
    allowed_supplement_scope: list[str] = Field(default_factory=list, max_length=64)
    reason: str = Field(max_length=400)


class RecoveryDecision(DomainModel):
    schema_version: Literal[1] = 1
    error: ErrorCategory
    action: Literal["supplement", "revalidate", "repair_arguments", "receipt", "stop", "clarify"]
    tool_index: int = -1
    attempt: int = Field(default=1, ge=1, le=1)
    succeeded: bool = False
    reason: str = Field(default="", max_length=400)


class TeammateDecision(DomainModel):
    schema_version: Literal[1] = 1
    mode: Literal["act", "speak", "assist", "pass"]
    target_id: str | None = None
    action_type: IntentType = "observe"
    action_text: str | None = Field(default=None, max_length=700)
    speech_text: str | None = Field(default=None, max_length=700)
    reason_summary: str = Field(default="", max_length=200)
    related_player_action_seq: int = Field(ge=1)
    related_public_entity_ids: list[str] = Field(default_factory=list, max_length=8)
    novelty_keys: list[str] = Field(default_factory=list, max_length=8)
    confidence: float = Field(ge=0, le=1)
    short_term_goal: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def has_output(self):
        if self.mode != "pass" and not (self.action_text or self.speech_text):
            raise ValueError("non_pass_requires_output")
        return self


class Cooldown(DomainModel):
    action_type: IntentType
    target_id: str | None
    remaining_cycles: int = Field(ge=0, le=10)
    state_fingerprint: str


class BehaviorState(DomainModel):
    schema_version: Literal[1] = 1
    current_short_term_goal: str = Field(default="", max_length=200)
    last_action_type: IntentType | None = None
    last_target_id: str | None = None
    last_public_output_hash: str | None = None
    recent_novelty_keys: list[str] = Field(default_factory=list, max_length=24)
    cooldowns: list[Cooldown] = Field(default_factory=list, max_length=24)
    last_acted_cycle: str | None = None
    consecutive_pass_count: int = Field(default=0, ge=0)
    updated_time: datetime = Field(default_factory=utc_now)


class BehaviorRejection(DomainModel):
    schema_version: Literal[1] = 1
    accepted: bool
    reason: str = Field(default="", max_length=300)
    repetition_score: float = Field(default=0, ge=0, le=1)


class SummaryRecoveryState(DomainModel):
    schema_version: Literal[1] = 1
    last_successful_summary_seq: int = 0
    pending_start_seq: int | None = None
    pending_end_seq: int | None = None
    stale: bool = False
    failure_count: int = 0
    last_safe_error: str | None = None
    next_retry_cycle: int = 0
    last_attempted_cycle: str | None = None
    last_attempted_time: datetime | None = None
    automatic_retry_stopped: bool = False


class SupplementBlock(DomainModel):
    block_id: str
    text: str = Field(max_length=420)


class SupplementNode(DomainModel):
    node_id: str
    blocks: list[SupplementBlock] = Field(default_factory=list, max_length=3)


class SupplementEntity(DomainModel):
    id: str
    type: str
    title: str
    public_summary: str = Field(max_length=400)
    keeper_summary: str = Field(max_length=400)
    reveal_conditions: RevealConditions


class SupplementContent(DomainModel):
    schema_version: Literal[1] = 1
    nodes: list[SupplementNode] = Field(default_factory=list, max_length=4)
    entities: list[SupplementEntity] = Field(default_factory=list, max_length=4)
    transitions: list[SceneTransition] = Field(default_factory=list, max_length=4)


class AdjudicationRecord(DomainModel):
    schema_version: Literal[1] = 1
    plan: KeeperPlan
    validation: ValidatedActionPlan | None = None
    context_gaps: list[ContextGap] = Field(default_factory=list)
    supplement: SupplementContent = Field(default_factory=SupplementContent)
    recoveries: list[RecoveryDecision] = Field(default_factory=list)
    supplement_attempted: bool = False
    revision_recovery_attempted: bool = False
    arguments_repair_attempted: bool = False
    narration: KeeperNarration | None = None


class ArgumentRepair(DomainModel):
    arguments: dict


class AdjudicationSaveState(DomainModel):
    schema_version: Literal[1] = 1
    behaviors: dict[str, BehaviorState] = Field(default_factory=dict)
    summary_recoveries: dict[str, SummaryRecoveryState] = Field(default_factory=dict)
