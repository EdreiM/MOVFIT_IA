import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SalesDaily(Base):
    """Vendas de um dia, por unidade da Pacto e por responsável pelo
    lançamento do contrato — a base do ranking de vendas. Guardar por DIA é o
    que permite à tela somar qualquer período sem refazer as consultas na
    Pacto (que são lentas: uma chamada por vendedor)."""

    __tablename__ = "sales_daily"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "chave_empresa", "empresa_id", "day", "responsavel_id", name="uq_sales_daily_key"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    chave_empresa: Mapped[str] = mapped_column(String(64))
    empresa_id: Mapped[int] = mapped_column(Integer)
    day: Mapped[date] = mapped_column(Date, index=True)
    # 0 = vendas que a Pacto contou mas que não foram atribuídas a ninguém.
    responsavel_id: Mapped[int] = mapped_column(Integer)
    responsavel_nome: Mapped[str] = mapped_column(String(255))
    # Todas as matrículas NOVAS do dia (qualquer plano).
    matriculas: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Só as de plano anual — o número do ranking.
    novas_anuais: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    rematriculas: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    renovacoes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # True quando a separação "matrícula anual × não anual" precisou de
    # estimativa (venda não anual do mesmo dia misturada com rematrícula).
    estimated: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SalesSyncDay(Base):
    """Dias já consultados na Pacto por unidade — inclusive os sem nenhuma
    venda (que não geram linha em SalesDaily), pra não reconsultar à toa."""

    __tablename__ = "sales_sync_days"
    __table_args__ = (
        UniqueConstraint("company_id", "chave_empresa", "empresa_id", "day", name="uq_sales_sync_days_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    chave_empresa: Mapped[str] = mapped_column(String(64))
    empresa_id: Mapped[int] = mapped_column(Integer)
    day: Mapped[date] = mapped_column(Date, index=True)
    matriculas_total: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
