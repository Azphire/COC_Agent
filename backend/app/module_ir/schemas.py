import hashlib
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.domain.character import DomainModel, utc_now

NodeType = Literal["document", "chapter", "section", "scene", "appendix", "block_group"]
BlockType = Literal[
    "paragraph", "heading", "list", "table", "read_aloud", "keeper_note", "stat_block", "unknown"
]
Detection = Literal[
    "word_style", "outline_level", "toc", "bookmark", "numbering", "format_heuristic", "host"
]


class Position(DomainModel):
    file_reference: str
    paragraph_start: int = Field(ge=0)
    paragraph_end: int = Field(ge=0)
    physical_page: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    page_label: str | None = None
    offset_start: int | None = None
    offset_end: int | None = None


class ModuleBlock(DomainModel):
    block_id: str
    node_id: str
    source_order: int = Field(ge=0)
    block_type: BlockType
    text: str
    table_structure: list[list[str]] | None = None
    style_name: str | None = None
    outline_level: int | None = Field(default=None, ge=0, le=9)
    numbering: str | None = None
    page_reference: int | None = Field(default=None, ge=1)
    source_position: Position
    visibility: Literal["keeper_only", "public"] = "keeper_only"
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    page_break_before: bool = False
    bookmark_names: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_hash(self):
        if hashlib.sha256(self.text.encode()).hexdigest() != self.content_hash:
            raise ValueError("block_content_hash_mismatch")
        return self


class ModuleNode(DomainModel):
    node_id: str
    parent_node_id: str | None = None
    child_ids: list[str] = Field(default_factory=list)
    source_order: int = Field(ge=0)
    depth: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=240)
    normalized_title: str
    heading_path: list[str]
    detected_type: NodeType
    approved_type: NodeType | None = None
    detection_source: Detection
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_position: Position
    included: bool = True
    visibility: Literal["keeper_only", "public"] = "keeper_only"
    public_title: str = Field(default="", max_length=120)
    public_summary: str = Field(default="", max_length=1000)
    keeper_summary: str = Field(default="", max_length=1000)
    linked_node_ids: list[str] = Field(default_factory=list, max_length=30)
    important_block_ids: list[str] = Field(default_factory=list, max_length=30)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


def validate_tree(nodes, root_id):
    by_id = {n.node_id: n for n in nodes}
    if len(by_id) != len(nodes) or root_id not in by_id:
        raise ValueError("duplicate_or_missing_node")
    seen = set()

    def visit(node, parent, path):
        if node.node_id in seen or node.parent_node_id != parent:
            raise ValueError("cyclic_or_inconsistent_tree")
        seen.add(node.node_id)
        if node.depth != len(path) or node.heading_path != [*path, node.title]:
            raise ValueError("inconsistent_heading_path")
        children = [by_id[c] for c in node.child_ids]
        if [c.source_order for c in children] != sorted(c.source_order for c in children):
            raise ValueError("source_order_changed")
        for child in children:
            visit(child, node.node_id, node.heading_path)

    try:
        visit(by_id[root_id], None, [])
    except KeyError as error:
        raise ValueError("missing_child") from error
    if seen != set(by_id):
        raise ValueError("unreachable_node")
    return by_id


class ModuleDocumentIR(DomainModel):
    schema_version: Literal[1] = 1
    module_id: str
    source_id: str
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    structure_version: str
    display_title: str
    source_format: str
    extraction_method: str
    root_node_id: str
    node_count: int
    block_count: int
    nodes: list[ModuleNode]
    blocks: list[ModuleBlock]
    warnings: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def consistent(self):
        by_id = validate_tree(self.nodes, self.root_node_id)
        if self.node_count != len(self.nodes) or self.block_count != len(self.blocks):
            raise ValueError("count_mismatch")
        if len({b.block_id for b in self.blocks}) != len(self.blocks):
            raise ValueError("duplicate_block")
        orders = [b.source_order for b in self.blocks]
        if orders != sorted(set(orders)):
            raise ValueError("block_order_changed")
        if any(b.node_id not in by_id for b in self.blocks):
            raise ValueError("block_node_missing")
        return self


class NodePatch(DomainModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    approved_type: NodeType | None = None
    parent_node_id: str | None = None
    included: bool | None = None
    public_title: str | None = Field(default=None, max_length=120)
    public_summary: str | None = Field(default=None, max_length=1000)
    keeper_summary: str | None = Field(default=None, max_length=1000)
    linked_node_ids: list[str] | None = Field(default=None, max_length=30)
    important_block_ids: list[str] | None = Field(default=None, max_length=30)
    initial_scene: bool = False


class EntityNodeBinding(DomainModel):
    binding_id: str
    entity_id: str
    node_id: str
    source_hash: str
    evidence_ids: list[str] = Field(default_factory=list)
    npc_entity_id: str | None = None


class BindingInput(DomainModel):
    entity_id: str
    node_id: str
    source_hash: str
    npc_entity_id: str | None = None


class SceneTransition(DomainModel):
    transition_id: str = ""
    source_scene_node_id: str
    target_scene_node_id: str
    transition_type: Literal["normal", "conditional", "host_only"] = "normal"
    condition_summary: str = Field(default="", max_length=600)
    required_revealed_entity_ids: list[str] = Field(default_factory=list, max_length=30)
    required_event_types: list[str] = Field(default_factory=list, max_length=30)
    approved: bool = False
    source_evidence: list[str] = Field(default_factory=list, max_length=12)
    relation_id: str | None = None


class StructureSnapshot(DomainModel):
    schema_version: Literal[1] = 1
    snapshot_id: str
    preparation_id: str
    preparation_version: int
    source_id: str
    source_hash: str
    structure_version: str
    root_node_id: str
    nodes: list[ModuleNode]
    entity_bindings: list[EntityNodeBinding] = Field(default_factory=list)
    transitions: list[SceneTransition] = Field(default_factory=list)
    initial_scene_node_id: str | None = None
    incomplete: bool = False
    approved: bool = False
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def consistent(self):
        by_id = validate_tree(self.nodes, self.root_node_id)
        for n in self.nodes:
            if not set(n.linked_node_ids) <= set(by_id):
                raise ValueError("linked_node_missing")
        for binding in self.entity_bindings:
            if binding.node_id not in by_id or binding.source_hash != self.source_hash:
                raise ValueError("binding_source_mismatch")
        if self.approved:
            initial = by_id.get(self.initial_scene_node_id)
            if not initial or not initial.included or initial.approved_type != "scene":
                raise ValueError("approved_initial_scene_required")
            for n in self.nodes:
                if n.included and not n.approved_type:
                    raise ValueError("included_node_requires_host_approval")
                if (
                    n.included
                    and n.approved_type == "scene"
                    and not (n.public_title.strip() and n.public_summary.strip())
                ):
                    raise ValueError("scene_public_summary_required")
        for t in self.transitions:
            for node_id in (t.source_scene_node_id, t.target_scene_node_id):
                node = by_id.get(node_id)
                if not node or not node.included or node.approved_type != "scene":
                    raise ValueError("transition_requires_scene")
        return self


class ApproveStructure(DomainModel):
    incomplete: bool = False


class TransitionRequest(DomainModel):
    target_scene_node_id: str
    expected_revision: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=100)


class RoomModuleNavigationState(DomainModel):
    schema_version: Literal[1] = 1
    room_id: str
    structure_snapshot_id: str
    structure_version: str
    module_source_hash: str
    current_scene_node_id: str
    previous_scene_node_id: str | None = None
    visited_scene_node_ids: list[str] = Field(default_factory=list)
    active_npc_entity_ids: list[str] = Field(default_factory=list)
    active_location_entity_ids: list[str] = Field(default_factory=list)
    revealed_entity_ids: list[str] = Field(default_factory=list)
    available_transition_ids: list[str] = Field(default_factory=list)
    navigation_revision: int = Field(default=0, ge=0)
    updated_event_seq: int = 0
    selected_node_ids: list[str] = Field(default_factory=list)
    selected_block_ids: list[str] = Field(default_factory=list)
    pending_transition: TransitionRequest | None = None
    pending_review_id: str | None = None
    module_structure_missing: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class NodeArgs(DomainModel):
    node_id: str


class LookupArgs(DomainModel):
    query: str = Field(min_length=1, max_length=120)


class ModuleSearchArgs(LookupArgs):
    query: str = Field(min_length=1, max_length=500)
    scope: Literal["current_scene", "linked_nodes", "global"] = "current_scene"
    top_k: int = Field(default=4, ge=1, le=6)
    scene_id: str | None = None
    entity_id: str | None = None
    topic: str | None = None


class SceneToolArgs(DomainModel):
    scene_id: str | None = None
    target_scene_node_id: str | None = None
    expected_revision: int | None = Field(default=None, ge=0)
    request_id: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def complete_request(self):
        if self.target_scene_node_id:
            if self.expected_revision is None or self.request_id is None or self.scene_id:
                raise ValueError("revision_and_request_id_required")
        elif not self.scene_id:
            raise ValueError("scene_required")
        return self
