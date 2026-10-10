"""add pacto connection and units

Revision ID: d5a9c2e7f4b1
Revises: c3e8f1a5d2b7
Create Date: 2026-10-10

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d5a9c2e7f4b1"
down_revision: Union[str, None] = "c3e8f1a5d2b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pacto_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("redirect_uri", sa.Text(), nullable=True),
        sa.Column("client_id", sa.String(length=255), nullable=True),
        sa.Column("client_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("pending_state", sa.String(length=128), nullable=True),
        sa.Column("pending_code_verifier", sa.String(length=128), nullable=True),
        sa.Column("access_token_encrypted", sa.Text(), nullable=True),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'disconnected'")),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_pacto_connections_company_id", "pacto_connections", ["company_id"], unique=True)
    op.create_table(
        "pacto_units",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chave_empresa", sa.String(length=64), nullable=False),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("pacto_name", sa.String(length=255), nullable=False),
        sa.Column(
            "unit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("units.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("movement", postgresql.JSONB(), nullable=True),
        sa.Column("movement_text", sa.Text(), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("company_id", "chave_empresa", "empresa_id", name="uq_pacto_units_company_chave_unit"),
    )
    op.create_index("ix_pacto_units_company_id", "pacto_units", ["company_id"])
    op.create_index("ix_pacto_units_unit_id", "pacto_units", ["unit_id"])


def downgrade() -> None:
    op.drop_table("pacto_units")
    op.drop_table("pacto_connections")
