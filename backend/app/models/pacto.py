import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PactoConnection(Base):
    """Conexão da empresa com o servidor de relatórios da Pacto (MCP com
    OAuth). Alguém autoriza UMA vez pelo painel; a partir daí o backend
    renova o acesso sozinho (refresh token) e busca os dados por conta
    própria — ver app/services/pacto.py. Tokens ficam criptografados em
    repouso, como as outras credenciais."""

    __tablename__ = "pacto_connections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), unique=True, index=True
    )
    # Cliente OAuth registrado no servidor da Pacto pra ESTE endereço de
    # retorno (registro dinâmico) — muda o endereço, registra de novo.
    redirect_uri: Mapped[str | None] = mapped_column(Text)
    client_id: Mapped[str | None] = mapped_column(String(255))
    client_secret_encrypted: Mapped[str | None] = mapped_column(Text)
    # Autorização em andamento (entre o clique em "Conectar" e a volta).
    pending_state: Mapped[str | None] = mapped_column(String(128))
    pending_code_verifier: Mapped[str | None] = mapped_column(String(128))
    access_token_encrypted: Mapped[str | None] = mapped_column(Text)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # disconnected | connected | error (acesso caiu — precisa reconectar)
    status: Mapped[str] = mapped_column(String(20), default="disconnected", server_default=text("'disconnected'"))
    last_error: Mapped[str | None] = mapped_column(String(500))
    # Desligado = a conexão continua, mas a IA não recebe os dados.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PactoUnit(Base):
    """Uma unidade da Pacto liberada na autorização, ligada (ou não) a uma
    unidade do catálogo da Mônica, com o resumo de movimento que a IA usa."""

    __tablename__ = "pacto_units"
    __table_args__ = (
        UniqueConstraint("company_id", "chave_empresa", "empresa_id", name="uq_pacto_units_company_chave_unit"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    chave_empresa: Mapped[str] = mapped_column(String(64))
    empresa_id: Mapped[int] = mapped_column(Integer)
    pacto_name: Mapped[str] = mapped_column(String(255))
    # Unidade correspondente em Unidades & Planos — sugerida pelo nome na
    # conexão e ajustável no painel. Sem vínculo, a unidade não vai pra IA.
    unit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("units.id", ondelete="SET NULL"), index=True
    )
    # {"by_hour": {"18": 10.5, ...}, "by_weekday": {"Quinta-Feira": 21.1, ...}}
    # — % dos acessos de catraca dos últimos 30 dias.
    movement: Mapped[dict | None] = mapped_column(JSONB)
    # Frase pronta que vai pro prompt da IA.
    movement_text: Mapped[str | None] = mapped_column(Text)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
