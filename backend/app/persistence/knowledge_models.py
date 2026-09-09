"""Additive knowledge references, never full document text in game saves."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.character import utc_now
from app.persistence.character_models import Base


class RoomKnowledgeBinding(Base):
    __tablename__ = "room_knowledge_bindings"
    room_id: Mapped[str] = mapped_column(ForeignKey("game_rooms.id"), primary_key=True)
    document: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RetrievalRecord(Base):
    __tablename__ = "agent_retrieval_records"
    id: Mapped[str] = mapped_column(primary_key=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("game_rooms.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    profile_id: Mapped[str]
    actor_member_id: Mapped[str]
    query: Mapped[str]
    query_hash: Mapped[str]
    source_filters: Mapped[dict] = mapped_column(JSON)
    evidence: Mapped[list] = mapped_column(JSON)
    injected_ids: Mapped[list] = mapped_column(JSON, default=list)
    visibility: Mapped[str] = mapped_column(default="host_only")
    latency_ms: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ClaimRecord(Base):
    __tablename__ = "agent_grounded_claims"
    id: Mapped[str] = mapped_column(primary_key=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("game_rooms.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"))
    document: Mapped[dict] = mapped_column(JSON)
    event_seq: Mapped[int | None]
    memory_id: Mapped[str | None]


class KnowledgeSaveState(Base):
    __tablename__ = "knowledge_save_states"
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("room_snapshots.id"), primary_key=True)
    document: Mapped[dict] = mapped_column(JSON)


class AgentModelCall(Base):
    __tablename__ = "agent_model_calls"
    id: Mapped[str] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    document: Mapped[dict] = mapped_column(JSON)
