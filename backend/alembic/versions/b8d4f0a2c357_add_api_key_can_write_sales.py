"""chave de API com permissão de enviar vendas (separada das chaves só-leitura)

Chaves antigas (ex.: a do link de métricas, que é compartilhada) continuam só
lendo; só uma chave marcada pode gravar os contratos do ranking de vendas.

Revision ID: b8d4f0a2c357
Revises: a7c3e9f1b246
Create Date: 2026-10-14 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "b8d4f0a2c357"
down_revision = "a7c3e9f1b246"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column("can_write_sales", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("api_keys", "can_write_sales")
