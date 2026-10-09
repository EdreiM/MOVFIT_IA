import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class KnowledgeGap(Base):
    """Pergunta de cliente que a IA não soube responder (disse que não tinha
    a informação, ou teve que encaminhar pra confirmar) — vira a lista do
    que falta cadastrar no catálogo ou na base de conhecimento. Chat de
    teste não gera linha aqui, mesmo critério das outras métricas."""

    __tablename__ = "knowledge_gaps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    question: Mapped[str] = mapped_column(Text)
    ai_reply: Mapped[str] = mapped_column(Text)
    # Como foi detectado: "sem_informacao" (a IA disse que não tinha) ou
    # "modalidade_fora_do_catalogo" (trava de diária/semanal).
    reason: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
