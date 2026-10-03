"""add reply_debounce_seconds to ai_configs

Revision ID: f2a9b1c3d4e5
Revises: e8a1c4f92b03
Create Date: 2026-10-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2a9b1c3d4e5"
down_revision: Union[str, None] = "e8a1c4f92b03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_configs",
        sa.Column(
            "reply_debounce_seconds",
            sa.Float(),
            nullable=False,
            server_default=sa.text("8"),
        ),
    )


def downgrade() -> None:
    op.drop_column("ai_configs", "reply_debounce_seconds")
