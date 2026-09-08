"""add unit to leads

Revision ID: a338f0eb32e1
Revises: 3e61eaee0204
Create Date: 2026-09-08 03:41:11.524203
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a338f0eb32e1'
down_revision: Union[str, None] = '3e61eaee0204'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('leads', sa.Column('unit', sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column('leads', 'unit')
