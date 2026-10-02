import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Promotion(Base):
    """Campanha/promoção configurável no painel — separada de Plan porque pode
    ser temporária, ter público específico e exigir transferência sem link."""

    __tablename__ = "promotions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_until: Mapped[date | None] = mapped_column(Date)
    # all | women | men | new_students
    audience: Mapped[str] = mapped_column(String(50), default="all")
    mention_on_plan_request: Mapped[bool] = mapped_column(Boolean, default=True)
    # Lista vazia = vale para todas as unidades.
    unit_ids: Mapped[list] = mapped_column(JSONB, default=list)
    requires_transfer: Mapped[bool] = mapped_column(Boolean, default=True)
    transfer_reason: Mapped[str | None] = mapped_column(String(500))
    trigger_keywords: Mapped[list] = mapped_column(JSONB, default=list)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
