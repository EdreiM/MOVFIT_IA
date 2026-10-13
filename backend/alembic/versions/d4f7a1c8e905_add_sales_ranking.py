"""add sales_daily e sales_sync_days (ranking de vendas)

Revision ID: d4f7a1c8e905
Revises: c9e2a7f4b816
Create Date: 2026-10-13 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "d4f7a1c8e905"
down_revision = "c9e2a7f4b816"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sales_daily",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chave_empresa", sa.String(64), nullable=False),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("responsavel_id", sa.Integer(), nullable=False),
        sa.Column("responsavel_nome", sa.String(255), nullable=False),
        sa.Column("matriculas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("novas_anuais", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rematriculas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("renovacoes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "company_id", "chave_empresa", "empresa_id", "day", "responsavel_id", name="uq_sales_daily_key"
        ),
    )
    op.create_index("ix_sales_daily_company_id", "sales_daily", ["company_id"])
    op.create_index("ix_sales_daily_day", "sales_daily", ["day"])

    op.create_table(
        "sales_sync_days",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chave_empresa", sa.String(64), nullable=False),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("matriculas_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("company_id", "chave_empresa", "empresa_id", "day", name="uq_sales_sync_days_key"),
    )
    op.create_index("ix_sales_sync_days_company_id", "sales_sync_days", ["company_id"])
    op.create_index("ix_sales_sync_days_day", "sales_sync_days", ["day"])


def downgrade() -> None:
    op.drop_table("sales_sync_days")
    op.drop_table("sales_daily")
