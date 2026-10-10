import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, UniqueConstraint, func, text
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
    # "Já agendou avaliação física alguma vez" (métrica, permanente) — os
    # dois campos abaixo guardam só o ÚLTIMO agendamento feito pela IA
    # (mutável, sobrescrito a cada novo agendamento), pra mostrar no
    # cadastro do cliente qual é o compromisso mais recente.
    physical_eval_scheduled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_physical_eval_date: Mapped[str | None] = mapped_column(String(8))  # yyyyMMdd
    last_physical_eval_time: Mapped[str | None] = mapped_column(String(5))  # HH:MM
    # Data (yyyyMMdd) da avaliação pra qual o lembrete da véspera JÁ foi
    # mandado — remarcar muda last_physical_eval_date, então o lembrete da
    # nova data sai de novo sem precisar zerar nada (ver
    # app/services/eval_reminders.py).
    physical_eval_reminder_for: Mapped[str | None] = mapped_column(String(8))
    # Qualquer outro dado que apareça no futuro (ex: profissão, objetivo,
    # indicação) sem precisar criar coluna nova pra cada campo novo.
    custom_fields: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Etiquetas automáticas "categoria:valor" (ex: "assunto:planos",
    # "objecao:preco", "unidade:Itaituba") — preenchidas pelo código a cada
    # resposta, ver app/services/lead_insights.py.
    tags: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    # Funil de VENDAS (interessado → qualificado → proposta → matriculado /
    # perdido), separado de `stage`: `stage` é o status do atendimento e é
    # sobrescrito por transferência/encerramento, o que apagaria o ponto do
    # funil em que o lead estava. Vazio = nunca entrou no funil (ex: aluno
    # tirando dúvida).
    sales_stage: Mapped[str | None] = mapped_column(String(30), index=True)
    lost_reason: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
