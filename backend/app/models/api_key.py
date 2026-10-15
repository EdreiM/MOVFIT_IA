import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ApiKey(Base):
    """Chave de acesso pra sistemas externos (n8n, BI, etc) consultarem
    dados só-leitura (leads, métricas) sem precisar de login de usuário —
    autenticação machine-to-machine, separada do JWT normal do painel."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Só o hash é guardado — a chave completa só existe no momento da
    # criação (devolvida uma vez na resposta), nunca mais recuperável.
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Só chaves marcadas podem GRAVAR os contratos do ranking de vendas (fluxo
    # do n8n); as demais são só-leitura, como a do link compartilhado de métricas.
    can_write_sales: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
