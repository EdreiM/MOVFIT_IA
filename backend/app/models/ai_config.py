import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AiConfig(Base):
    """Uma linha "padrão" por empresa (integration_id nulo) mais, opcionalmente,
    uma linha por integração — permite nome/personalidade/chave de API
    diferentes por integração (ex: GYMBOT com um tom e uma chave, outro
    sistema com outro) sem duplicar RAGs/ferramentas, que continuam ligadas
    à empresa como um todo, não a essa personalização."""

    __tablename__ = "ai_configs"
    __table_args__ = (
        UniqueConstraint("company_id", "integration_id", name="uq_ai_configs_company_integration"),
        Index(
            "uq_ai_configs_company_default",
            "company_id",
            unique=True,
            postgresql_where=text("integration_id IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    # Nulo = configuração padrão da empresa (usada quando a conversa não tem
    # integração, ou a integração não tem personalização própria).
    integration_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("integrations.id", ondelete="CASCADE"), index=True
    )
    ai_name: Mapped[str] = mapped_column(String(100), default="Mônica")
    # Descrição curta de personalidade (ex: "caloroso, informal, usa gírias
    # leves") — junto com use_emoji, monta o prompt de tom automaticamente.
    tone: Mapped[str | None] = mapped_column(Text)
    use_emoji: Mapped[bool] = mapped_column(Boolean, default=True)
    # Instruções extras opcionais, além de nome/tom/emoji — pra regra
    # específica que não caiba nos campos estruturados de cima.
    system_prompt: Mapped[str] = mapped_column(
        Text,
        default="Você é a Mônica, assistente virtual de atendimento. Seja cordial, objetiva e útil.",
    )
    llm_provider: Mapped[str] = mapped_column(String(50), default="openai")
    llm_model: Mapped[str] = mapped_column(String(100), default="gpt-4o-mini")
    llm_api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    temperature: Mapped[float] = mapped_column(Float, default=0.3)
    operation_mode: Mapped[str] = mapped_column(String(50), default="auto")  # auto | suggest | off
    # Follow-up de cliente inativo: reengaja depois de X minutos sem resposta
    # do cliente, até um número máximo de tentativas — depois disso, encerra
    # sozinha. Configurável por integração, igual o resto da personalidade.
    followup_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    followup_delay_minutes: Mapped[int] = mapped_column(Integer, default=120)
    followup_max_attempts: Mapped[int] = mapped_column(Integer, default=2)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    company = relationship("Company", back_populates="ai_configs")
    rag_sources = relationship("RagSource", back_populates="ai_config", cascade="all, delete-orphan")
    tools = relationship("Tool", back_populates="ai_config", cascade="all, delete-orphan")


class RagSource(Base):
    __tablename__ = "rag_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    ai_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_configs.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), default="knowledge")
    webhook_url: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_latency_ms: Mapped[float | None] = mapped_column(Float)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ai_config = relationship("AiConfig", back_populates="rag_sources")


class Tool(Base):
    __tablename__ = "tools"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    ai_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_configs.id", ondelete="CASCADE"), index=True
    )
    # Em branco = ferramenta global, vale pra conversas de qualquer integração
    # (comportamento de sempre). Preenchida = só é usada em conversas vindas
    # dessa integração específica — permite, por exemplo, duas ferramentas
    # "enviar_imagens_planos" com webhooks diferentes por integração.
    integration_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("integrations.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Identificador estável enviado ao webhook e usado p/ efeitos internos
    # (transferir_atendimento, encerrar_atendimento). Ferramentas customizadas
    # usam um slug livre e são tratadas de forma 100% genérica.
    tool_key: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # Lista de parâmetros que a IA deve extrair da conversa e mandar no webhook:
    # [{"name": "cpf", "type": "string", "description": "...", "required": true}, ...]
    parameters: Mapped[list] = mapped_column(JSONB, default=list)
    tool_type: Mapped[str] = mapped_column(String(50), default="webhook")  # webhook | native
    webhook_url: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ai_config = relationship("AiConfig", back_populates="tools")
