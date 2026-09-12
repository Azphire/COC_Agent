"""Prepared interactions contain rules; room state contains their durable effects."""

from typing import Literal

from pydantic import Field, StrictBool, field_validator

from app.domain.character import DomainModel


class ModuleReward(DomainModel):
    formula: str
    surviving_npc_id: str | None = None
    required_flags: dict[str, StrictBool] = Field(default_factory=dict)
    all_survive: bool = False

    @field_validator("formula")
    @classmethod
    def valid_formula(cls, value):
        from app.rules.combat import damage_bounds

        low, high = damage_bounds(value)
        if low < 0 or high > 99:
            raise ValueError("SAN奖励必须为0到99的常数或骰式")
        return value


class RequiredSanitySettlement(DomainModel):
    entity_id: str
    effect_id: str


class ModuleInteraction(DomainModel):
    id: str = Field(min_length=1, max_length=80)
    instruction: str = Field(min_length=1, max_length=600)
    source_block_ids: list[str] = Field(min_length=1, max_length=20)
    required_flags: dict[str, StrictBool] = Field(default_factory=dict)
    required_item_ids: list[str] = Field(default_factory=list, max_length=12)
    required_entity_ids: list[str] = Field(default_factory=list, max_length=12)
    check_name: str | None = None
    check_passed: bool = True
    failure_interaction_id: str | None = None
    opposed_npc_id: str | None = None
    host_review: bool = False
    kp_enabled: bool = False
    situation: str = Field(default="", max_length=600)
    scene_node_ids: list[str] = Field(default_factory=list, max_length=12)
    check_difficulty: Literal["regular", "hard", "extreme"] = "regular"
    inventory_operation: (
        Literal["give", "drop", "pickup", "consume", "initial", "recover"] | None
    ) = None
    item_id: str | None = None
    consume_amount: int = Field(default=1, ge=1, le=20)
    once_per_actor: bool = False
    encounter_operation: (
        Literal[
            "sound_once",
            "sound_start",
            "sound_stop",
            "continuous_lure",
            "close_door",
            "open_door",
            "grab",
            "release",
            "carry",
            "put_down",
            "carry_check",
        ]
        | None
    ) = None
    sound_item_id: str | None = None
    allow_worn_sound_item: bool = False
    door_id: str | None = None
    npc_id: str | None = None
    carry_attribute: Literal["str", "con"] | None = None
    set_flags: dict[str, StrictBool] = Field(default_factory=dict)
    acquire_item_ids: list[str] = Field(default_factory=list, max_length=12)
    reveal_entity_ids: list[str] = Field(default_factory=list, max_length=12)
    following_npc_ids: list[str] = Field(default_factory=list, max_length=4)
    blocked_scene_node_ids: list[str] = Field(default_factory=list, max_length=12)
    outcome: str | None = Field(default=None, max_length=80)
    requires_party_loss: bool = False
    prepare_outcome: str | None = Field(default=None, max_length=80)
    required_sanity: list[RequiredSanitySettlement] = Field(default_factory=list, max_length=4)
    public_result: str = Field(min_length=1, max_length=1000)
    max_npc_count: int | None = Field(default=None, ge=1, le=3)
    san_rewards: list[ModuleReward] = Field(default_factory=list, max_length=3)
    mythos_reward: int = Field(default=0, ge=0, le=10)
    san_zero: bool = False


class PreparedCheckAdjustment(DomainModel):
    id: str = Field(min_length=1, max_length=80)
    kind: Literal["skill", "attribute"]
    name: str
    source_block_ids: list[str] = Field(min_length=1, max_length=12)
    required_flags: dict[str, StrictBool] = Field(default_factory=dict)
    required_entity_ids: list[str] = Field(default_factory=list, max_length=12)
    forbidden_entity_ids: list[str] = Field(default_factory=list, max_length=12)
    numerator: int = Field(default=1, ge=1, le=2)
    denominator: int = Field(default=1, ge=1, le=2)
    add: int = Field(default=0, ge=-80, le=20)
    subtract_die: Literal["1d20"] | None = None
    basis: str = Field(min_length=1, max_length=500)
    opposed_only: bool = False
    combat_only: bool = False
    scene_node_ids: list[str] = Field(default_factory=list, max_length=12)


class ModuleRuntimeState(DomainModel):
    flags: dict[str, StrictBool] = Field(default_factory=dict)
    inventory: dict[str, str] = Field(default_factory=dict)
    dropped_items: dict[str, str] = Field(default_factory=dict)
    consumed_items: dict[str, int] = Field(default_factory=dict)
    initial_belongings: dict[str, dict] = Field(default_factory=dict)
    item_instances: dict[str, str] = Field(default_factory=dict)
    sounds: dict[str, dict] = Field(default_factory=dict)
    sound_attention: dict[str, str] = Field(default_factory=dict)
    doors: dict[str, bool] = Field(default_factory=dict)
    grapples: dict[str, list[str]] = Field(default_factory=dict)
    carriers: dict[str, str] = Field(default_factory=dict)
    carry_checks: dict[str, dict] = Field(default_factory=dict)
    rulings: dict[str, dict] = Field(default_factory=dict)
    following_npc_ids: list[str] = Field(default_factory=list)
    npc_locations: dict[str, str] = Field(default_factory=dict)
    npc_counts: dict[str, int] = Field(default_factory=dict)
    blocked_scene_node_ids: list[str] = Field(default_factory=list)
    receipts: dict[str, dict] = Field(default_factory=dict)
    outcome: str | None = None
    pending_outcome: str | None = None


class ModuleActionArgs(DomainModel):
    entity_id: str
    interaction_id: str
    evidence_quote: str = Field(min_length=1, max_length=1000)
    recipient_member_id: str | None = None
    npc_instance_id: str | None = None
    used_item_id: str | None = None


class HostModuleAction(ModuleActionArgs):
    source_event_seq: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)
    mode: Literal["confirm"] = "confirm"
