"""zera o cache do ranking de vendas (rematrícula passa a contar)

A regra do ranking mudou: rematrícula passou a contar como venda nova
(confirmado pela supervisora). Os dias já coletados foram calculados sem ela e
os mais antigos não são reconferidos sozinhos — então o cache (sales_daily e
sales_sync_days) é apagado, e a próxima rodada coleta tudo de novo com a regra
nova. São só dados derivados da Pacto; nada se perde.

Revision ID: f3c9d2a7b418
Revises: d4f7a1c8e905
Create Date: 2026-10-14 00:00:00.000000

"""
from alembic import op


revision = "f3c9d2a7b418"
down_revision = "d4f7a1c8e905"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM sales_daily")
    op.execute("DELETE FROM sales_sync_days")


def downgrade() -> None:
    # Dados derivados: a próxima coleta repovoa.
    pass
