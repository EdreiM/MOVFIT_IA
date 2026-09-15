"""add transcription_api_key_encrypted to ai_configs

Revision ID: d4b6f0a83e17
Revises: c1d9e6a72f83
Create Date: 2026-09-17 21:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd4b6f0a83e17'
down_revision: Union[str, None] = 'c1d9e6a72f83'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('ai_configs', sa.Column('transcription_api_key_encrypted', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('ai_configs', 'transcription_api_key_encrypted')
