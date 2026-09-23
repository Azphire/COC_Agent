"""Resumable launch intent and host defaults; old rooms and cards are unchanged."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.character import utc_now
from app.persistence.character_models import Base


class LaunchDraft(Base):
    __tablename__ = "launch_drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), unique=True)
    request_hash: Mapped[str]
    version: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(default="draft")
    room_id: Mapped[str | None] = mapped_column(String(36), unique=True)
    document: Mapped[dict] = mapped_column(JSON)
    steps: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class LaunchDefault(Base):
    __tablename__ = "launch_defaults"

    key: Mapped[str] = mapped_column(primary_key=True)
    document: Mapped[dict] = mapped_column(JSON)
