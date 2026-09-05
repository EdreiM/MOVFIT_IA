"""add tool_key and parameters to tools

Revision ID: 9f1b6c2d4a01
Revises: 82c13a53c5c9
Create Date: 2026-09-04 13:10:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '9f1b6c2d4a01'
down_revision: Union[str, None] = '82c13a53c5c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'tools',
        sa.Column('tool_key', sa.String(length=100), nullable=False, server_default='custom'),
    )
    op.add_column(
        'tools',
        sa.Column('parameters', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
    )


def downgrade() -> None:
    op.drop_column('tools', 'parameters')
    op.drop_column('tools', 'tool_key')
