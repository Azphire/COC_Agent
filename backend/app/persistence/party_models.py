from datetime import datetime

from sqlalchemy import JSON, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.character import utc_now
from app.persistence.character_models import Base


class PartyBatch(Base):
    __tablename__ = "party_generation_batches"
    id: Mapped[str] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(unique=True)
    document: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
