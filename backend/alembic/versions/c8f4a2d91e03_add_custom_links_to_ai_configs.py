"""add custom_links to ai_configs

Revision ID: c8f4a2d91e03
Revises: b3e7f28a1c94
Create Date: 2026-09-18 09:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "c8f4a2d91e03"
down_revision: Union[str, None] = "b3e7f28a1c94"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_configs",
        sa.Column(
            "custom_links",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("ai_configs", "custom_links")
