"""add physical_eval_recommendations_sent to conversations

Revision ID: c1d9e6a72f83
Revises: a7c2e5f19d34
Create Date: 2026-09-17 20:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c1d9e6a72f83'
down_revision: Union[str, None] = 'a7c2e5f19d34'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'conversations',
        sa.Column('physical_eval_recommendations_sent', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    )


def downgrade() -> None:
    op.drop_column('conversations', 'physical_eval_recommendations_sent')
