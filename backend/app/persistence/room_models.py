from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.character_models import Base


class GameRoom(Base):
    __tablename__ = "game_rooms"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), default="lobby")
    host_member_id: Mapped[str] = mapped_column(String(36))
    invite_hash: Mapped[str] = mapped_column(String(64), unique=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    state_version: Mapped[int] = mapped_column(Integer, default=1)
    session_state: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RoomMember(Base):
    __tablename__ = "room_members"
    __table_args__ = (
        Index(
            "uq_room_active_name",
            "room_id",
            "name_key",
            unique=True,
            sqlite_where=text("active = 1"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("game_rooms.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    controller_type: Mapped[str] = mapped_column(String(16))
    access_type: Mapped[str] = mapped_column(String(16))
    display_name: Mapped[str] = mapped_column(String(120))
    name_key: Mapped[str] = mapped_column(String(240))
    ready: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RoomCharacterSlot(Base):
    __tablename__ = "room_character_slots"
    __table_args__ = (UniqueConstraint("room_id", "source_character_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("game_rooms.id"), index=True)
    source_character_id: Mapped[str] = mapped_column(String(36))
    character_snapshot: Mapped[dict] = mapped_column(JSON)
    public_summary: Mapped[dict] = mapped_column(JSON)
    member_id: Mapped[str | None] = mapped_column(ForeignKey("room_members.id"), unique=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RoomEvent(Base):
    __tablename__ = "room_events"
    __table_args__ = (UniqueConstraint("room_id", "actor_member_id", "client_request_id"),)

    room_id: Mapped[str] = mapped_column(ForeignKey("game_rooms.id"), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(64))
    actor_member_id: Mapped[str | None] = mapped_column(String(36))
    visibility: Mapped[str] = mapped_column(String(24))
    payload: Mapped[dict] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    client_request_id: Mapped[str | None] = mapped_column(String(36))
    request_hash: Mapped[str | None] = mapped_column(String(64))


class RoomSnapshot(Base):
    __tablename__ = "room_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("game_rooms.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    creator_id: Mapped[str] = mapped_column(String(36))
    event_seq: Mapped[int] = mapped_column(Integer)
    format_version: Mapped[int] = mapped_column(Integer, default=1)
    document: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
