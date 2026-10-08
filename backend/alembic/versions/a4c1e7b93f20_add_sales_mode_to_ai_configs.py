"""add sales_mode_enabled to ai_configs

Revision ID: a4c1e7b93f20
Revises: f2a9b1c3d4e5
Create Date: 2026-10-08

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a4c1e7b93f20"
down_revision: Union[str, None] = "f2a9b1c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_configs",
        sa.Column(
            "sales_mode_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("ai_configs", "sales_mode_enabled")
