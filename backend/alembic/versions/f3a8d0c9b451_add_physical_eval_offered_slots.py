"""add physical_eval_offered_slots to conversations

Revision ID: f3a8d0c9b451
Revises: e19a7c3d5f02
Create Date: 2026-09-17 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'f3a8d0c9b451'
down_revision: Union[str, None] = 'e19a7c3d5f02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'conversations',
        sa.Column('physical_eval_offered_slots', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('conversations', 'physical_eval_offered_slots')
