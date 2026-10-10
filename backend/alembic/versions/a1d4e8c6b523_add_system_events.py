"""add system_events (alertas e relatórios diários)

Revision ID: a1d4e8c6b523
Revises: f2c8d5b17a30
Create Date: 2026-10-10 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "a1d4e8c6b523"
down_revision = "f2c8d5b17a30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False, server_default="info"),
        sa.Column("key", sa.String(120), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_system_events_company_id", "system_events", ["company_id"])
    op.create_index("ix_system_events_kind", "system_events", ["kind"])
    op.create_index("ix_system_events_key", "system_events", ["key"])
    op.create_index("ix_system_events_created_at", "system_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("system_events")
