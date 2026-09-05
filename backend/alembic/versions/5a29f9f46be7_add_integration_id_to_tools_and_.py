"""add integration_id to tools and conversations

Revision ID: 5a29f9f46be7
Revises: 795073ada47f
Create Date: 2026-09-05 13:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '5a29f9f46be7'
down_revision: Union[str, None] = '795073ada47f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('tools', sa.Column('integration_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index(op.f('ix_tools_integration_id'), 'tools', ['integration_id'], unique=False)
    op.create_foreign_key(
        'tools_integration_id_fkey', 'tools', 'integrations', ['integration_id'], ['id'], ondelete='SET NULL'
    )

    op.add_column('conversations', sa.Column('integration_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index(op.f('ix_conversations_integration_id'), 'conversations', ['integration_id'], unique=False)
    op.create_foreign_key(
        'conversations_integration_id_fkey',
        'conversations',
        'integrations',
        ['integration_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('conversations_integration_id_fkey', 'conversations', type_='foreignkey')
    op.drop_index(op.f('ix_conversations_integration_id'), table_name='conversations')
    op.drop_column('conversations', 'integration_id')

    op.drop_constraint('tools_integration_id_fkey', 'tools', type_='foreignkey')
    op.drop_index(op.f('ix_tools_integration_id'), table_name='tools')
    op.drop_column('tools', 'integration_id')
