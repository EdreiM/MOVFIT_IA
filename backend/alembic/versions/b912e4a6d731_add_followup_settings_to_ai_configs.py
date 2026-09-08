"""add followup settings to ai_configs

Revision ID: b912e4a6d731
Revises: 7dc413d284b4
Create Date: 2026-09-08 05:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b912e4a6d731'
down_revision: Union[str, None] = '7dc413d284b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'ai_configs',
        sa.Column('followup_enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
    )
    op.add_column(
        'ai_configs',
        sa.Column('followup_delay_minutes', sa.Integer(), nullable=False, server_default=sa.text('120')),
    )
    op.add_column(
        'ai_configs',
        sa.Column('followup_max_attempts', sa.Integer(), nullable=False, server_default=sa.text('2')),
    )


def downgrade() -> None:
    op.drop_column('ai_configs', 'followup_max_attempts')
    op.drop_column('ai_configs', 'followup_delay_minutes')
    op.drop_column('ai_configs', 'followup_enabled')
