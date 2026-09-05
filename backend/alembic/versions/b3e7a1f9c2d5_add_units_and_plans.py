"""add units and plans

Revision ID: b3e7a1f9c2d5
Revises: 9f1b6c2d4a01
Create Date: 2026-09-04 14:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'b3e7a1f9c2d5'
down_revision: Union[str, None] = '9f1b6c2d4a01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'units',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('company_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('companies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('city', sa.String(length=255), nullable=False),
        sa.Column('unit_type', sa.String(length=100)),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
    )
    op.create_index('ix_units_company_id', 'units', ['company_id'])

    op.create_table(
        'plans',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('company_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('companies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('unit_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('units.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('monthly_price', sa.Float(), nullable=False),
        sa.Column('enrollment_fee', sa.Float(), nullable=False, server_default='0'),
        sa.Column('fidelity_months', sa.Integer()),
        sa.Column('payment_info', sa.String(length=255)),
        sa.Column('benefits', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('image_url', sa.Text()),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
    )
    op.create_index('ix_plans_company_id', 'plans', ['company_id'])
    op.create_index('ix_plans_unit_id', 'plans', ['unit_id'])


def downgrade() -> None:
    op.drop_table('plans')
    op.drop_table('units')
