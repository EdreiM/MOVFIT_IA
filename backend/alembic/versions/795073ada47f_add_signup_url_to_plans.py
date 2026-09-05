"""add signup_url to plans

Revision ID: 795073ada47f
Revises: b3e7a1f9c2d5
Create Date: 2026-09-05 04:53:25.892878
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '795073ada47f'
down_revision: Union[str, None] = 'b3e7a1f9c2d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('plans', sa.Column('signup_url', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('plans', 'signup_url')
