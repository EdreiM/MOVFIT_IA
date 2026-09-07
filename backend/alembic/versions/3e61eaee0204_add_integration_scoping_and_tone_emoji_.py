"""add integration scoping and tone/emoji fields to ai_configs

Revision ID: 3e61eaee0204
Revises: 4e7055604acf
Create Date: 2026-09-07 21:21:36.800435
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '3e61eaee0204'
down_revision: Union[str, None] = '4e7055604acf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('ai_configs', sa.Column('integration_id', sa.UUID(), nullable=True))
    op.add_column('ai_configs', sa.Column('tone', sa.Text(), nullable=True))
    op.add_column(
        'ai_configs',
        sa.Column('use_emoji', sa.Boolean(), nullable=False, server_default=sa.text('true')),
    )
    op.create_foreign_key(
        'fk_ai_configs_integration_id', 'ai_configs', 'integrations', ['integration_id'], ['id'], ondelete='CASCADE'
    )
    op.create_index(op.f('ix_ai_configs_integration_id'), 'ai_configs', ['integration_id'], unique=False)

    # A unicidade antiga era só em company_id (uma linha por empresa). Troca
    # por: única por (company_id, integration_id), mais um índice parcial
    # garantindo só uma linha "padrão" (integration_id nulo) por empresa.
    op.drop_constraint('ai_configs_company_id_key', 'ai_configs', type_='unique')
    op.create_index(op.f('ix_ai_configs_company_id'), 'ai_configs', ['company_id'], unique=False)
    op.create_unique_constraint('uq_ai_configs_company_integration', 'ai_configs', ['company_id', 'integration_id'])
    op.create_index(
        'uq_ai_configs_company_default',
        'ai_configs',
        ['company_id'],
        unique=True,
        postgresql_where=sa.text('integration_id IS NULL'),
    )


def downgrade() -> None:
    op.drop_index('uq_ai_configs_company_default', table_name='ai_configs', postgresql_where=sa.text('integration_id IS NULL'))
    op.drop_constraint('uq_ai_configs_company_integration', 'ai_configs', type_='unique')
    op.drop_index(op.f('ix_ai_configs_company_id'), table_name='ai_configs')
    op.create_unique_constraint('ai_configs_company_id_key', 'ai_configs', ['company_id'])

    op.drop_index(op.f('ix_ai_configs_integration_id'), table_name='ai_configs')
    op.drop_constraint('fk_ai_configs_integration_id', 'ai_configs', type_='foreignkey')
    op.drop_column('ai_configs', 'use_emoji')
    op.drop_column('ai_configs', 'tone')
    op.drop_column('ai_configs', 'integration_id')
