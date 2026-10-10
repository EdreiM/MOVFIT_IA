import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class LlmUsage(Base):
    """Uma linha por chamada ao modelo de IA, com os tokens que o provedor
    cobrou e o custo em dólar calculado NA HORA (pela tabela de preços de
    app/services/llm_usage.py) — assim uma mudança de preço depois não
    reescreve o histórico. É a base do "Custo da IA" no painel: total por
    mês e custo por cliente.

    Chat de teste também entra (gasta de verdade), marcado pelo canal da
    conversa — o painel separa do custo de atendimento real."""

    __tablename__ = "llm_usage"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    # SET NULL: apagar uma conversa não apaga o gasto que ela gerou.
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), index=True
    )
    model: Mapped[str] = mapped_column(String(100))
    # resposta | followup | imagem
    purpose: Mapped[str] = mapped_column(String(30))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    # Parte de prompt_tokens que veio do cache do provedor (mais barata).
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    # Vazio quando o modelo não está na tabela de preços.
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 8))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
