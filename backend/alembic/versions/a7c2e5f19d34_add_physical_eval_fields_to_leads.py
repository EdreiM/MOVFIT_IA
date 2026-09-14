"""add physical eval scheduling fields to leads

Revision ID: a7c2e5f19d34
Revises: f3a8d0c9b451
Create Date: 2026-09-17 19:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a7c2e5f19d34'
down_revision: Union[str, None] = 'f3a8d0c9b451'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'leads', sa.Column('physical_eval_scheduled', sa.Boolean(), nullable=False, server_default=sa.text('false'))
    )
    op.add_column('leads', sa.Column('last_physical_eval_date', sa.String(length=8), nullable=True))
    op.add_column('leads', sa.Column('last_physical_eval_time', sa.String(length=5), nullable=True))


def downgrade() -> None:
    op.drop_column('leads', 'last_physical_eval_time')
    op.drop_column('leads', 'last_physical_eval_date')
    op.drop_column('leads', 'physical_eval_scheduled')
