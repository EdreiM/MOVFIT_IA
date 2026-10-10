import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SystemEvent(Base):
    """Alertas de saúde da operação e relatórios diários.

    Alerta: existe enquanto a condição existir (ferramenta falhando,
    transferência sem atendente, cliente sem resposta) e é fechado sozinho
    (resolved_at) quando ela passa. `key` identifica a condição pra não criar
    o mesmo alerta de novo a cada varredura. `dismissed_at` é alguém ter
    dispensado no painel: some da lista, mas o alerta segue "aberto" até a
    condição passar — senão voltaria a aparecer na varredura seguinte.
    Relatório: um texto por dia (key = "daily:AAAA-MM-DD")."""

    __tablename__ = "system_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20), index=True)  # alert | report
    severity: Mapped[str] = mapped_column(String(20), default="info")  # info | warning | critical
    key: Mapped[str] = mapped_column(String(120), index=True)
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
