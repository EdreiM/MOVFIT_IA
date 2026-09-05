"""add ai_name to ai_configs

Revision ID: 82c13a53c5c9
Revises: 0001_initial
Create Date: 2026-09-04 12:24:51.359840
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '82c13a53c5c9'
down_revision: Union[str, None] = '0001_initial'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'ai_configs',
        sa.Column('ai_name', sa.String(length=100), nullable=False, server_default='Mônica'),
    )


def downgrade() -> None:
    op.drop_column('ai_configs', 'ai_name')
