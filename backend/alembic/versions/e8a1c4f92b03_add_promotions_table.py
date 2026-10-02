"""add promotions table

Revision ID: e8a1c4f92b03
Revises: d581a2f4c7e6
Create Date: 2026-10-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e8a1c4f92b03"
down_revision: Union[str, None] = "c8f4a2d91e03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "promotions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("audience", sa.String(50), nullable=False, server_default="all"),
        sa.Column("mention_on_plan_request", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("unit_ids", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("requires_transfer", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("transfer_reason", sa.String(500), nullable=True),
        sa.Column("trigger_keywords", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_promotions_company_id", "promotions", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_promotions_company_id", table_name="promotions")
    op.drop_table("promotions")
