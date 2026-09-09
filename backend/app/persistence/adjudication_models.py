"""Additive records for plans, teammate behavior and summary recovery."""

from sqlalchemy import JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.character_models import Base


class ActionPlanRecord(Base):
    __tablename__ = "agent_action_plans"
    cycle_id: Mapped[str] = mapped_column(ForeignKey("agent_cycles.id"), primary_key=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("game_rooms.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"))
    document: Mapped[dict] = mapped_column(JSON)


class AgentBehaviorRecord(Base):
    __tablename__ = "agent_behavior_states"
    room_id: Mapped[str] = mapped_column(ForeignKey("game_rooms.id"), primary_key=True)
    member_id: Mapped[str] = mapped_column(ForeignKey("room_members.id"), primary_key=True)
    document: Mapped[dict] = mapped_column(JSON)


class SummaryRecoveryRecord(Base):
    __tablename__ = "summary_recovery_states"
    room_id: Mapped[str] = mapped_column(ForeignKey("game_rooms.id"), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("agent_profiles.id"), primary_key=True)
    document: Mapped[dict] = mapped_column(JSON)
