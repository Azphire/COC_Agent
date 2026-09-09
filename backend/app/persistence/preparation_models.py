"""Additive preparation tables; JSON documents have strict service-level schemas."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.character import utc_now
from app.persistence.character_models import Base


class ModulePreparation(Base):
    __tablename__ = "module_preparations"
    id: Mapped[str] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(index=True)
    source_hash: Mapped[str]
    status: Mapped[str] = mapped_column(default="created")
    version: Mapped[int] = mapped_column(default=1)
    document: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class GenerationRun(Base):
    __tablename__ = "module_generation_runs"
    id: Mapped[str] = mapped_column(primary_key=True)
    preparation_id: Mapped[str] = mapped_column(ForeignKey("module_preparations.id"), index=True)
    status: Mapped[str]
    evidence: Mapped[list] = mapped_column(JSON)
    calls: Mapped[list] = mapped_column(JSON, default=list)
    safe_error: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ModuleEntity(Base):
    __tablename__ = "module_entities"
    id: Mapped[str] = mapped_column(primary_key=True)
    preparation_id: Mapped[str] = mapped_column(ForeignKey("module_preparations.id"), index=True)
    generation_run_id: Mapped[str | None] = mapped_column(ForeignKey("module_generation_runs.id"))
    type: Mapped[str]
    status: Mapped[str]
    version: Mapped[int] = mapped_column(default=1)
    document: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ModuleEntityRelation(Base):
    __tablename__ = "module_entity_relations"
    id: Mapped[str] = mapped_column(primary_key=True)
    preparation_id: Mapped[str] = mapped_column(ForeignKey("module_preparations.id"), index=True)
    source_entity_id: Mapped[str] = mapped_column(ForeignKey("module_entities.id"))
    target_entity_id: Mapped[str] = mapped_column(ForeignKey("module_entities.id"))
    relation_type: Mapped[str]
    status: Mapped[str]
    document: Mapped[dict] = mapped_column(JSON)


class RoomPreparationBinding(Base):
    __tablename__ = "room_module_preparation_bindings"
    room_id: Mapped[str] = mapped_column(ForeignKey("game_rooms.id"), primary_key=True)
    preparation_id: Mapped[str] = mapped_column(ForeignKey("module_preparations.id"))
    source_id: Mapped[str]
    source_hash: Mapped[str]
    preparation_version: Mapped[int]
    initial_scene: Mapped[str]
    current_scene: Mapped[str]
    relations: Mapped[list] = mapped_column(JSON)
    bound_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RoomEntityState(Base):
    __tablename__ = "room_entity_states"
    room_id: Mapped[str] = mapped_column(ForeignKey("game_rooms.id"), primary_key=True)
    source_entity_id: Mapped[str] = mapped_column(primary_key=True)
    entity_type: Mapped[str]
    snapshot: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(default="hidden")
    frozen_public_summary: Mapped[str]
    frozen_source_references: Mapped[list] = mapped_column(JSON)
    revealed_event_seq: Mapped[int | None]
    revealed_by: Mapped[str | None]
    revealed_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    correction_reference: Mapped[int | None]


class HostReviewRequest(Base):
    __tablename__ = "host_review_requests"
    __table_args__ = (
        UniqueConstraint("cycle_id", name="uq_one_review_per_cycle"),
        Index(
            "uq_pending_room_review",
            "room_id",
            unique=True,
            sqlite_where=text("status = 'pending'"),
        ),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("game_rooms.id"), index=True)
    cycle_id: Mapped[str] = mapped_column(ForeignKey("agent_cycles.id"))
    agent_run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"))
    request_type: Mapped[str]
    status: Mapped[str]
    document: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PreparationSaveState(Base):
    __tablename__ = "preparation_save_states"
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("room_snapshots.id"), primary_key=True)
    document: Mapped[dict] = mapped_column(JSON)
