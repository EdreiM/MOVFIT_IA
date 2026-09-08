import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ToolCallLog(Base):
    """Um registro por chamada de ferramenta (webhook n8n) feita pela IA —
    separado do WebhookLog (que é sobre tráfego de integração genérico),
    pra dar métricas por ferramenta (quem pediu link de parcela, quem viu
    planos, taxa de sucesso por ferramenta etc) sem depender de vasculhar
    JSON de log de webhook. Chat de teste não gera linha aqui, mesmo
    critério já usado nas outras métricas."""

    __tablename__ = "tool_call_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    # FK opcional (SET NULL) + tool_key/tool_name copiados: se a ferramenta
    # for excluída depois, o histórico de métricas não some junto.
    tool_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tools.id", ondelete="SET NULL"), index=True
    )
    tool_key: Mapped[str] = mapped_column(String(100), index=True)
    tool_name: Mapped[str] = mapped_column(String(255))
    success: Mapped[bool] = mapped_column(Boolean)
    arguments: Mapped[dict] = mapped_column(JSONB, default=dict)
    error_message: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
