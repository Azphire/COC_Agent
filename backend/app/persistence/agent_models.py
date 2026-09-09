"""Additive tables; existing character and multiplayer schemas are untouched."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.character import utc_now
from app.persistence.character_models import Base


class ProfileRecord(Base):
    __tablename__ = "agent_profiles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    role: Mapped[str]
    document: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ModuleSnapshot(Base):
    __tablename__ = "module_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("game_rooms.id"), unique=True)
    content_hash: Mapped[str]
    document: Mapped[dict] = mapped_column(JSON)
    state: Mapped[dict] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RoomAgentBinding(Base):
    __tablename__ = "room_agent_bindings"
    __table_args__ = (
        Index(
            "uq_enabled_agent_seat",
            "room_id",
            "member_id",
            unique=True,
            sqlite_where=text("enabled = 1"),
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("game_rooms.id"), index=True)
    member_id: Mapped[str] = mapped_column(ForeignKey("room_members.id"))
    profile_id: Mapped[str] = mapped_column(ForeignKey("agent_profiles.id"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_consumed_event_seq: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(default="idle")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AgentCycle(Base):
    __tablename__ = "agent_cycles"
    __table_args__ = (
        Index(
            "uq_active_room_cycle",
            "room_id",
            unique=True,
            sqlite_where=text("status IN ('running', 'waiting_for_roll', 'failed')"),
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("game_rooms.id"), index=True)
    status: Mapped[str]
    state: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CheckRecord(Base):
    __tablename__ = "pending_checks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("game_rooms.id"), index=True)
    cycle_id: Mapped[str] = mapped_column(ForeignKey("agent_cycles.id"))
    target_member_id: Mapped[str]
    agent_run_id: Mapped[str]
    status: Mapped[str]
    document: Mapped[dict] = mapped_column(JSON)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("game_rooms.id"), index=True)
    cycle_id: Mapped[str] = mapped_column(ForeignKey("agent_cycles.id"))
    profile_id: Mapped[str] = mapped_column(ForeignKey("agent_profiles.id"))
    actor_member_id: Mapped[str]
    graph_node: Mapped[str]
    status: Mapped[str]
    input_seq_start: Mapped[int]
    input_seq_end: Mapped[int]
    provider: Mapped[str]
    model: Mapped[str]
    context: Mapped[dict] = mapped_column(JSON)
    structured_output: Mapped[dict | None] = mapped_column(JSON)
    tool_results: Mapped[list] = mapped_column(JSON, default=list)
    error_type: Mapped[str | None]
    safe_error: Mapped[str | None]
    latency_ms: Mapped[int] = mapped_column(default=0)
    token_usage: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentMemory(Base):
    __tablename__ = "agent_memories"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("game_rooms.id"), index=True)
    profile_id: Mapped[str | None] = mapped_column(ForeignKey("agent_profiles.id"))
    kind: Mapped[str]
    scope: Mapped[str]
    content: Mapped[str]
    source_event_ids: Mapped[list] = mapped_column(JSON)
    salience: Mapped[int]
    supersedes_id: Mapped[str | None]
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    coverage_start: Mapped[int | None]
    coverage_end: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ToolReceipt(Base):
    __tablename__ = "agent_tool_receipts"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("game_rooms.id"))
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"))
    request_hash: Mapped[str]
    result: Mapped[dict] = mapped_column(JSON)


class AgentSaveState(Base):
    __tablename__ = "agent_save_states"
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("room_snapshots.id"), primary_key=True)
    document: Mapped[dict] = mapped_column(JSON)
