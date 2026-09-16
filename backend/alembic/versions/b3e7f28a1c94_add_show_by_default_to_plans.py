"""add show_by_default to plans

Revision ID: b3e7f28a1c94
Revises: d4b6f0a83e17
Create Date: 2026-09-16 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "b3e7f28a1c94"
down_revision = "d4b6f0a83e17"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "plans",
        sa.Column("show_by_default", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("plans", "show_by_default")
