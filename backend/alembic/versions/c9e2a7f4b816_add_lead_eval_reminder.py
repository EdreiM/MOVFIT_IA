"""add leads.physical_eval_reminder_for (lembrete da véspera da avaliação)

Revision ID: c9e2a7f4b816
Revises: a1d4e8c6b523
Create Date: 2026-10-10 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "c9e2a7f4b816"
down_revision = "a1d4e8c6b523"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("physical_eval_reminder_for", sa.String(8), nullable=True))


def downgrade() -> None:
    op.drop_column("leads", "physical_eval_reminder_for")
