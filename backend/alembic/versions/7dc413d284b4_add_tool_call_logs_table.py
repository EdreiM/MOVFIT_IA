"""add tool_call_logs table

Revision ID: 7dc413d284b4
Revises: a338f0eb32e1
Create Date: 2026-09-08 04:10:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '7dc413d284b4'
down_revision: Union[str, None] = 'a338f0eb32e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tool_call_logs',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('company_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('conversation_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tool_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('tool_key', sa.String(length=100), nullable=False),
        sa.Column('tool_name', sa.String(length=255), nullable=False),
        sa.Column('success', sa.Boolean(), nullable=False),
        sa.Column('arguments', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('error_message', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tool_id'], ['tools.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_tool_call_logs_company_id'), 'tool_call_logs', ['company_id'])
    op.create_index(op.f('ix_tool_call_logs_conversation_id'), 'tool_call_logs', ['conversation_id'])
    op.create_index(op.f('ix_tool_call_logs_tool_id'), 'tool_call_logs', ['tool_id'])
    op.create_index(op.f('ix_tool_call_logs_tool_key'), 'tool_call_logs', ['tool_key'])
    op.create_index(op.f('ix_tool_call_logs_created_at'), 'tool_call_logs', ['created_at'])


def downgrade() -> None:
    op.drop_index(op.f('ix_tool_call_logs_created_at'), table_name='tool_call_logs')
    op.drop_index(op.f('ix_tool_call_logs_tool_key'), table_name='tool_call_logs')
    op.drop_index(op.f('ix_tool_call_logs_tool_id'), table_name='tool_call_logs')
    op.drop_index(op.f('ix_tool_call_logs_conversation_id'), table_name='tool_call_logs')
    op.drop_index(op.f('ix_tool_call_logs_company_id'), table_name='tool_call_logs')
    op.drop_table('tool_call_logs')
