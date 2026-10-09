"""add lead tags, sales funnel, handoff summary and knowledge gaps

Revision ID: b7d2c9e4a1f3
Revises: a4c1e7b93f20
Create Date: 2026-10-09

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7d2c9e4a1f3"
down_revision: Union[str, None] = "a4c1e7b93f20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column("tags", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column("leads", sa.Column("sales_stage", sa.String(length=30), nullable=True))
    op.add_column("leads", sa.Column("lost_reason", sa.String(length=60), nullable=True))
    op.create_index("ix_leads_sales_stage", "leads", ["sales_stage"])
    op.add_column("conversations", sa.Column("handoff_summary", sa.Text(), nullable=True))
    op.create_table(
        "knowledge_gaps",
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
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("ai_reply", sa.Text(), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_knowledge_gaps_company_id", "knowledge_gaps", ["company_id"])
    op.create_index("ix_knowledge_gaps_conversation_id", "knowledge_gaps", ["conversation_id"])
    op.create_index("ix_knowledge_gaps_created_at", "knowledge_gaps", ["created_at"])


def downgrade() -> None:
    op.drop_table("knowledge_gaps")
    op.drop_column("conversations", "handoff_summary")
    op.drop_index("ix_leads_sales_stage", table_name="leads")
    op.drop_column("leads", "lost_reason")
    op.drop_column("leads", "sales_stage")
    op.drop_column("leads", "tags")
