"""add leads table

Revision ID: 4e7055604acf
Revises: 5a29f9f46be7
Create Date: 2026-09-06 18:23:00.197127
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '4e7055604acf'
down_revision: Union[str, None] = '5a29f9f46be7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'leads',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('company_id', sa.UUID(), nullable=False),
        sa.Column('phone', sa.String(length=50), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=True),
        sa.Column('cpf', sa.String(length=20), nullable=True),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('birthdate', sa.Date(), nullable=True),
        sa.Column('stage', sa.String(length=50), nullable=False),
        sa.Column('custom_fields', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('company_id', 'phone', name='uq_leads_company_phone'),
    )
    op.create_index(op.f('ix_leads_company_id'), 'leads', ['company_id'], unique=False)
    op.create_index(op.f('ix_leads_phone'), 'leads', ['phone'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_leads_phone'), table_name='leads')
    op.drop_index(op.f('ix_leads_company_id'), table_name='leads')
    op.drop_table('leads')
