import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MetricsDaily(Base):
    __tablename__ = "metrics_daily"
    __table_args__ = (UniqueConstraint("company_id", "day", name="uq_metrics_company_day"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    conversations_total: Mapped[int] = mapped_column(Integer, default=0)
    messages_inbound: Mapped[int] = mapped_column(Integer, default=0)
    messages_outbound: Mapped[int] = mapped_column(Integer, default=0)
    ai_resolved: Mapped[int] = mapped_column(Integer, default=0)
    human_resolved: Mapped[int] = mapped_column(Integer, default=0)
    avg_response_seconds: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
