import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Lead(Base):
    """Cadastro estruturado do cliente/lead — separado da conversa, pra não
    depender da janela de histórico de mensagens pra "lembrar" de dados como
    CPF, e-mail, ou em que estágio do funil ele está. Uma linha por telefone
    dentro da empresa (o mesmo número pode gerar várias conversas ao longo do
    tempo, mas é a mesma pessoa)."""

    __tablename__ = "leads"
    __table_args__ = (UniqueConstraint("company_id", "phone", name="uq_leads_company_phone"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    phone: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    cpf: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(255))
    birthdate: Mapped[date | None] = mapped_column(Date)
    # Unidade onde o cliente já é aluno confirmado — vem de uma ferramenta
    # que consultou sistema externo com "unidade" + "cpf" juntos (sinal de
    # matrícula real), não do que ele mencionou de passagem na conversa.
    unit: Mapped[str | None] = mapped_column(String(255))
    # Estágio no funil — texto livre (ex: novo, qualificado, transferido,
    # matriculado, perdido) em vez de enum fixo, porque cada empresa pode
    # querer nomear/adicionar estágios diferentes sem precisar de migração.
    stage: Mapped[str] = mapped_column(String(50), default="novo")
    # Flags "permanentes" pra métricas — ao contrário de `stage` (um valor
    # só, sobrescrito a cada mudança), essas nunca voltam a False depois de
    # viradas True. Existem porque `stage` sozinho não dá pra responder
    # "quantos são alunos" se um aluno depois for transferido (o stage dele
    # vira "transferido" e a informação de que é aluno se perderia).
    is_student: Mapped[bool] = mapped_column(Boolean, default=False)
    was_transferred: Mapped[bool] = mapped_column(Boolean, default=False)
    wants_cancellation: Mapped[bool] = mapped_column(Boolean, default=False)
    # Qualquer outro dado que apareça no futuro (ex: profissão, objetivo,
    # indicação) sem precisar criar coluna nova pra cada campo novo.
    custom_fields: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
