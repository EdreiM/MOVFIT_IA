"""add sticky flags to leads

Revision ID: e19a7c3d5f02
Revises: d581a2f4c7e6
Create Date: 2026-09-08 21:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e19a7c3d5f02'
down_revision: Union[str, None] = 'd581a2f4c7e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('leads', sa.Column('is_student', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column(
        'leads', sa.Column('was_transferred', sa.Boolean(), nullable=False, server_default=sa.text('false'))
    )
    op.add_column(
        'leads', sa.Column('wants_cancellation', sa.Boolean(), nullable=False, server_default=sa.text('false'))
    )


def downgrade() -> None:
    op.drop_column('leads', 'wants_cancellation')
    op.drop_column('leads', 'was_transferred')
    op.drop_column('leads', 'is_student')
