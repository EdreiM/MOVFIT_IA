"""add human support hours to ai_configs

Revision ID: f2c8d5b17a30
Revises: e7b3c1a94d62
Create Date: 2026-10-10 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "f2c8d5b17a30"
down_revision = "e7b3c1a94d62"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_configs",
        sa.Column("human_hours_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "ai_configs",
        sa.Column("human_hours_start", sa.String(5), nullable=False, server_default="07:00"),
    )
    op.add_column(
        "ai_configs",
        sa.Column("human_hours_end", sa.String(5), nullable=False, server_default="22:00"),
    )
    op.add_column(
        "ai_configs",
        sa.Column("human_hours_days", sa.String(20), nullable=False, server_default="0,1,2,3,4,5"),
    )


def downgrade() -> None:
    op.drop_column("ai_configs", "human_hours_days")
    op.drop_column("ai_configs", "human_hours_end")
    op.drop_column("ai_configs", "human_hours_start")
    op.drop_column("ai_configs", "human_hours_enabled")
