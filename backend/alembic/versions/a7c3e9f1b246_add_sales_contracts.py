"""contratos de venda do ranking (fonte: n8n -> resumo-pessoa da Pacto)

Revision ID: a7c3e9f1b246
Revises: f3c9d2a7b418
Create Date: 2026-10-14 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "a7c3e9f1b246"
down_revision = "f3c9d2a7b418"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sales_contracts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("unidade", sa.String(100), nullable=False),
        sa.Column("empresa_nome", sa.String(255), nullable=False),
        sa.Column("contrato", sa.Integer, nullable=False),
        sa.Column("matricula", sa.String(32)),
        sa.Column("cliente_nome", sa.String(255)),
        sa.Column("responsavel", sa.String(255)),
        sa.Column("consultor", sa.String(255)),
        sa.Column("plano", sa.String(255)),
        sa.Column("duracao", sa.Integer),
        sa.Column("produto", sa.String(64), nullable=False),
        sa.Column("lancamento", sa.Date, nullable=False),
        sa.Column("vendido_em", sa.DateTime(timezone=True)),
        sa.Column("inicio", sa.Date),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("company_id", "unidade", "contrato", name="uq_sales_contracts_key"),
    )
    op.create_index("ix_sales_contracts_company_id", "sales_contracts", ["company_id"])
    op.create_index("ix_sales_contracts_lancamento", "sales_contracts", ["lancamento"])


def downgrade() -> None:
    op.drop_index("ix_sales_contracts_lancamento", table_name="sales_contracts")
    op.drop_index("ix_sales_contracts_company_id", table_name="sales_contracts")
    op.drop_table("sales_contracts")
