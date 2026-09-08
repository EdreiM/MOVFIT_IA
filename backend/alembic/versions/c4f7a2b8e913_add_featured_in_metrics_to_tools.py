"""add featured_in_metrics to tools

Revision ID: c4f7a2b8e913
Revises: b912e4a6d731
Create Date: 2026-09-08 13:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c4f7a2b8e913'
down_revision: Union[str, None] = 'b912e4a6d731'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'tools',
        sa.Column('featured_in_metrics', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    )


def downgrade() -> None:
    op.drop_column('tools', 'featured_in_metrics')
