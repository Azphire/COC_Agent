from typing import Literal

from pydantic import Field, model_validator

from app.agents.modules import SuggestedCheck
from app.domain.character import DomainModel
from app.knowledge.schemas import SourceRef
from app.preparation.runtime_schemas import ModuleInteraction, PreparedCheckAdjustment
from app.rooms.combat_schemas import CombatTemplate
from app.rooms.sanity_schemas import SanityEffect
from app.rules.compound import NPCCheckStats

EntityType = Literal["scene", "npc", "location", "clue", "item"]
ReviewStatus = Literal["draft", "approved", "rejected"]
RelationType = Literal["located_in", "appears_in", "reveals", "requires", "leads_to", "related_to"]


class Scope(DomainModel):
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    section: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def ordered(self):
        if self.page_start and self.page_end and self.page_start > self.page_end:
            raise ValueError("页码范围顺序错误")
        return self


class PreparationInput(SourceRef):
    display_title: str = Field(min_length=1, max_length=120)
    scope: Scope = Field(default_factory=Scope)
    model_preset: Literal["default"] = "default"


class RevealConditions(DomainModel):
    access_policy: (
        Literal["automatic", "requires_check", "requires_condition", "host_review"] | None
    ) = None
    scene_id: str | None = Field(default=None, max_length=80)
    required_entity_ids: list[str] = Field(default_factory=list, max_length=12)
    successful_check: SuggestedCheck | None = None
    note: str = Field(default="", max_length=400)


class EntityFields(DomainModel):
    dialogue_topics: list[dict] = Field(default_factory=list, max_length=12)
    aliases: list[str] = Field(default_factory=list, max_length=16)
    search_aliases: list[str] = Field(default_factory=list, max_length=16)
    check_adjustments: list[PreparedCheckAdjustment] = Field(default_factory=list, max_length=12)
    interactions: list[ModuleInteraction] = Field(default_factory=list, max_length=20)
    combat_template: CombatTemplate | None = None
    source_block_ids: list[str] = Field(default_factory=list, max_length=60)
    reviewed_by: str = Field(default="", max_length=120)
    review_basis: str = Field(default="", max_length=500)
    check_stats: NPCCheckStats | None = None
    sanity_effects: list[SanityEffect] = Field(default_factory=list, max_length=8)
    type: EntityType
    title: str = Field(min_length=1, max_length=120)
    keeper_summary: str = Field(default="", max_length=1600)
    public_summary: str = Field(default="", max_length=1000)
    initial_visibility: Literal["hidden", "revealed"] = "hidden"
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)
    source_pages: list[int] = Field(default_factory=list, max_length=12)
    confidence: float | None = Field(default=None, ge=0, le=1)
    suggested_checks: list[SuggestedCheck] = Field(default_factory=list, max_length=4)
    reveal_conditions: RevealConditions = Field(default_factory=RevealConditions)
    tags: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def distinct_sanity_effects(self):
        if self.combat_template and self.type != "npc":
            raise ValueError("只有 NPC 可以准备战斗模板")
        if self.check_stats and self.type != "npc":
            raise ValueError("只有 NPC 可以准备对抗数值")
        if len({e.id for e in self.sanity_effects}) != len(self.sanity_effects):
            raise ValueError("SAN 效果 ID 不能重复")
        return self


class EntityDraft(EntityFields):
    check_adjustments: list[PreparedCheckAdjustment] = Field(default_factory=list, max_length=0)
    interactions: list[ModuleInteraction] = Field(default_factory=list, max_length=0)
    combat_template: None = None
    reviewed_by: Literal[""] = ""
    # Numeric NPC preparation must be confirmed by a human, never invented by generation.
    check_stats: None = None
    # Require an explicit public decision, including an intentional empty value.
    # Otherwise constrained generation can legally omit every useful summary.
    public_summary: str = Field(max_length=1000)
    evidence_ids: list[str] = Field(max_length=12)
    local_id: str = Field(min_length=1, max_length=80)
    existing_entity_id: str | None = Field(default=None, max_length=80)


class RelationDraft(DomainModel):
    source_entity_id: str = Field(min_length=1, max_length=80)
    target_entity_id: str = Field(min_length=1, max_length=80)
    relation_type: RelationType
    keeper_note: str = Field(default="", max_length=600)
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)


class GenerationOutput(DomainModel):
    entities: list[EntityDraft] = Field(max_length=6)
    relations: list[RelationDraft] = Field(default_factory=list, max_length=8)


class HostEntityInput(EntityFields):
    preparation_id: str


class EntityPatch(DomainModel):
    aliases: list[str] | None = Field(default=None, max_length=16)
    search_aliases: list[str] | None = Field(default=None, max_length=16)
    dialogue_topics: list[dict] | None = Field(default=None, max_length=12)
    check_adjustments: list[PreparedCheckAdjustment] | None = Field(default=None, max_length=12)
    interactions: list[ModuleInteraction] | None = Field(default=None, max_length=20)
    combat_template: CombatTemplate | None = None
    source_block_ids: list[str] | None = Field(default=None, max_length=60)
    reviewed_by: str | None = Field(default=None, max_length=120)
    review_basis: str | None = Field(default=None, max_length=500)
    check_stats: NPCCheckStats | None = None
    sanity_effects: list[SanityEffect] | None = Field(default=None, max_length=8)
    type: EntityType | None = None
    title: str | None = Field(default=None, min_length=1, max_length=120)
    keeper_summary: str | None = Field(default=None, max_length=1600)
    public_summary: str | None = Field(default=None, max_length=1000)
    initial_visibility: Literal["hidden", "revealed"] | None = None
    suggested_checks: list[SuggestedCheck] | None = Field(default=None, max_length=4)
    reveal_conditions: RevealConditions | None = None
    tags: list[str] | None = Field(default=None, max_length=12)


class PreparationPatch(DomainModel):
    initial_scene_entity_id: str
    required_entity_ids: list[str] = Field(default_factory=list, max_length=30)


class BindPreparation(DomainModel):
    preparation_id: str


class EntityArgs(DomainModel):
    entity_id: str = Field(min_length=1, max_length=80)


class ProposalArgs(DomainModel):
    request_type: Literal["reveal_entity", "scene_transition", "module_fact"] = "module_fact"
    entity_type: EntityType = "clue"
    proposed_title: str = Field(min_length=1, max_length=120)
    proposed_public_summary: str = Field(default="", max_length=1000)
    keeper_reason: str = Field(default="", max_length=800)
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)
    related_entity_ids: list[str] = Field(default_factory=list, max_length=12)
    entity_id: str | None = Field(default=None, max_length=80)


class ReviewResponse(DomainModel):
    host_response: str = Field(default="", max_length=800)
    public_summary: str | None = Field(default=None, max_length=1000)
    entity_type: EntityType | None = None


class Correction(DomainModel):
    public_summary: str = Field(min_length=1, max_length=1000)
    reason: str = Field(min_length=1, max_length=400)
