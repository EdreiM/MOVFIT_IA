"""add llm_usage table and usd_brl_rate to ai_configs

Revision ID: c3e8f1a5d2b7
Revises: b7d2c9e4a1f3
Create Date: 2026-10-10

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3e8f1a5d2b7"
down_revision: Union[str, None] = "b7d2c9e4a1f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_usage",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("purpose", sa.String(length=30), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cached_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(14, 8), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_llm_usage_company_id", "llm_usage", ["company_id"])
    op.create_index("ix_llm_usage_conversation_id", "llm_usage", ["conversation_id"])
    op.create_index("ix_llm_usage_created_at", "llm_usage", ["created_at"])
    op.add_column(
        "ai_configs",
        sa.Column("usd_brl_rate", sa.Float(), nullable=False, server_default=sa.text("5.0")),
    )
    op.add_column(
        "ai_configs",
        sa.Column("usd_brl_rate_auto", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "ai_configs",
        sa.Column("usd_brl_rate_updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_configs", "usd_brl_rate_updated_at")
    op.drop_column("ai_configs", "usd_brl_rate_auto")
    op.drop_column("ai_configs", "usd_brl_rate")
    op.drop_table("llm_usage")
