"""Additive game tables. All document columns are validated by module_ir.schemas."""

from sqlalchemy import JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.character_models import Base


class StructureOverride(Base):
    __tablename__ = "module_structure_overrides"
    preparation_id: Mapped[str] = mapped_column(
        ForeignKey("module_preparations.id"), primary_key=True
    )
    document: Mapped[dict] = mapped_column(JSON)
    approved_snapshot_id: Mapped[str | None]


class ApprovedStructure(Base):
    __tablename__ = "module_structure_snapshots"
    id: Mapped[str] = mapped_column(primary_key=True)
    preparation_id: Mapped[str] = mapped_column(ForeignKey("module_preparations.id"), index=True)
    document: Mapped[dict] = mapped_column(JSON)


class EntityNodeBindingRecord(Base):
    __tablename__ = "module_entity_node_bindings"
    id: Mapped[str] = mapped_column(primary_key=True)
    preparation_id: Mapped[str] = mapped_column(ForeignKey("module_preparations.id"), index=True)
    document: Mapped[dict] = mapped_column(JSON)


class NavigationRecord(Base):
    __tablename__ = "room_module_navigation_states"
    room_id: Mapped[str] = mapped_column(ForeignKey("game_rooms.id"), primary_key=True)
    document: Mapped[dict] = mapped_column(JSON)


class NavigationReceipt(Base):
    __tablename__ = "module_navigation_receipts"
    room_id: Mapped[str] = mapped_column(ForeignKey("game_rooms.id"), primary_key=True)
    request_id: Mapped[str] = mapped_column(primary_key=True)
    request_hash: Mapped[str]
    result: Mapped[dict] = mapped_column(JSON)


class NavigationSaveState(Base):
    __tablename__ = "module_navigation_save_states"
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("room_snapshots.id"), primary_key=True)
    document: Mapped[dict] = mapped_column(JSON)
