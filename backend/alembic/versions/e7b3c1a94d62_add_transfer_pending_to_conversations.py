"""add transfer pending fields to conversations

Revision ID: e7b3c1a94d62
Revises: d5a9c2e7f4b1
Create Date: 2026-10-10 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "e7b3c1a94d62"
down_revision = "d5a9c2e7f4b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations", sa.Column("transfer_pending_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("conversations", sa.Column("transfer_pending_reason", sa.Text(), nullable=True))
    op.add_column(
        "conversations",
        sa.Column("transfer_attempts", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("conversations", "transfer_attempts")
    op.drop_column("conversations", "transfer_pending_reason")
    op.drop_column("conversations", "transfer_pending_at")
