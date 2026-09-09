from typing import Literal

from pydantic import Field, field_validator

from app.domain.character import DomainModel

Kind = Literal["rulebook", "investigator_handbook", "module"]
Hash = str


class SourceManifest(DomainModel):
    source_id: str | None = Field(default=None, pattern=r"^[\w-]{1,80}$")
    module_id: str | None = Field(default=None, pattern=r"^[\w-]{1,80}$")
    title: str | None = Field(default=None, min_length=1, max_length=120)
    edition: str = Field(default="coc7", max_length=40)
    language: str = Field(default="zh", max_length=40)
    version: str | None = Field(default=None, max_length=40)


class KnowledgeSource(SourceManifest):
    source_id: str
    title: str
    kind: Kind
    visibility: Literal["public_rules", "keeper_only"]
    relative_reference: str
    mime_type: str
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    extraction_status: str = "pending"
    indexed_at: str | None = None
    page_count: int = 0
    extracted_pages: int = 0
    chunk_count: int = 0
    files: list[dict] = Field(default_factory=list)
    error_summary: str | None = None


class RetrievedEvidence(DomainModel):
    evidence_id: str
    chunk_id: str
    source_id: str
    source_hash: str
    source_title: str
    source_kind: Kind
    edition: str
    section: str | None = None
    physical_page: int | None = None
    page_label: str | None = None
    page_kind: Literal["pdf", "word", "text"] = "text"
    file_name: str
    excerpt: str = Field(max_length=420)
    score: float
    rank: int
    visibility: Literal["public_rules", "keeper_only"]
    module_id: str | None = None
    scene_id: str | None = None
    entity_id: str | None = None


class SearchArgs(DomainModel):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=4, ge=1, le=6)
    topic: str | None = Field(default=None, max_length=80)
    scene_id: str | None = Field(default=None, max_length=80)
    entity_id: str | None = Field(default=None, max_length=80)


class ExcerptArgs(DomainModel):
    evidence_id: str = Field(min_length=1, max_length=80)


class SourceRef(DomainModel):
    source_id: str = Field(min_length=1, max_length=80)
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class KnowledgeBinding(DomainModel):
    rules: list[SourceRef] = Field(default_factory=list, max_length=8)
    module: SourceRef | None = None
    enabled: bool = True
    # Host-authored public scene; never automatically copied from raw module text.
    opening_scene: str | None = Field(default=None, max_length=1000)


class GroundedClaim(DomainModel):
    claim_id: str = Field(min_length=1, max_length=80)
    category: Literal["rule", "module_fact", "flavor"]
    statement: str = Field(min_length=1, max_length=700)
    evidence_ids: list[str] = Field(default_factory=list, max_length=6)
    entity_ids: list[str] = Field(default_factory=list, max_length=6)
    node_ids: list[str] = Field(default_factory=list, max_length=6)
    basis_type: (
        Literal["module_evidence", "approved_entity", "module_node", "host_authored"] | None
    ) = None
    visibility: Literal["public", "keeper_only"] = "public"

    @field_validator("evidence_ids", "entity_ids")
    @classmethod
    def unique(cls, value):
        return list(dict.fromkeys(value))


class GroundedNarration(DomainModel):
    claims: list[GroundedClaim] = Field(default_factory=list, max_length=5)
    needs_host_ruling: bool = False
