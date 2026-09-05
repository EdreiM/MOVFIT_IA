"""Initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False, unique=True),
        sa.Column("status", sa.String(50), server_default="active"),
        sa.Column("plan", sa.String(50), server_default="starter"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_companies_slug", "companies", ["slug"])

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("role", sa.String(50), server_default="admin"),
        sa.Column("status", sa.String(50), server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "user_companies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "company_id", name="uq_user_company"),
    )

    op.create_table(
        "numbers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE")),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(50), nullable=False),
        sa.Column("channel_type", sa.String(50), server_default="whatsapp_qr"),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("webhook_mode", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("config", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_numbers_company_id", "numbers", ["company_id"])
    op.create_index("ix_numbers_phone", "numbers", ["phone"])

    op.create_table(
        "ai_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), unique=True),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("llm_provider", sa.String(50), server_default="openai"),
        sa.Column("llm_model", sa.String(100), server_default="gpt-4o-mini"),
        sa.Column("llm_api_key_encrypted", sa.Text()),
        sa.Column("temperature", sa.Float(), server_default="0.3"),
        sa.Column("operation_mode", sa.String(50), server_default="auto"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "rag_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE")),
        sa.Column("ai_config_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ai_configs.id", ondelete="CASCADE")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("source_type", sa.String(50), server_default="knowledge"),
        sa.Column("webhook_url", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("last_latency_ms", sa.Float()),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "tools",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE")),
        sa.Column("ai_config_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ai_configs.id", ondelete="CASCADE")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("tool_type", sa.String(50), server_default="webhook"),
        sa.Column("webhook_url", sa.Text()),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("last_executed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "integrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("integration_type", sa.String(50), server_default="webhook"),
        sa.Column("adapter_key", sa.String(100), server_default="generic_mapping"),
        sa.Column("inbound_secret", sa.String(255)),
        sa.Column("outbound_url", sa.Text()),
        sa.Column("field_mapping", postgresql.JSONB()),
        sa.Column("config", postgresql.JSONB()),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "webhook_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="SET NULL")),
        sa.Column("integration_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("integrations.id", ondelete="SET NULL")),
        sa.Column("direction", sa.String(20), server_default="inbound"),
        sa.Column("status", sa.String(50), server_default="received"),
        sa.Column("http_status", sa.Integer()),
        sa.Column("error_message", sa.Text()),
        sa.Column("payload", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE")),
        sa.Column("number_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("numbers.id", ondelete="SET NULL")),
        sa.Column("external_conversation_id", sa.String(255)),
        sa.Column("contact_phone", sa.String(50), nullable=False),
        sa.Column("contact_name", sa.String(255)),
        sa.Column("status", sa.String(50), server_default="open"),
        sa.Column("ai_enabled", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("channel", sa.String(50)),
        sa.Column("last_message_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_conversations_company_id", "conversations", ["company_id"])
    op.create_index("ix_conversations_contact_phone", "conversations", ["contact_phone"])
    op.create_index("ix_conversations_external_conversation_id", "conversations", ["external_conversation_id"])

    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="CASCADE")),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE")),
        sa.Column("external_message_id", sa.String(255)),
        sa.Column("direction", sa.String(20), nullable=False),
        sa.Column("actor", sa.String(50), server_default="customer"),
        sa.Column("content_type", sa.String(50), server_default="text"),
        sa.Column("text", sa.Text()),
        sa.Column("raw_payload", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_index("ix_messages_company_id", "messages", ["company_id"])

    op.create_table(
        "metrics_daily",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE")),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("conversations_total", sa.Integer(), server_default="0"),
        sa.Column("messages_inbound", sa.Integer(), server_default="0"),
        sa.Column("messages_outbound", sa.Integer(), server_default="0"),
        sa.Column("ai_resolved", sa.Integer(), server_default="0"),
        sa.Column("human_resolved", sa.Integer(), server_default="0"),
        sa.Column("avg_response_seconds", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("company_id", "day", name="uq_metrics_company_day"),
    )


def downgrade() -> None:
    op.drop_table("metrics_daily")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("webhook_logs")
    op.drop_table("integrations")
    op.drop_table("tools")
    op.drop_table("rag_sources")
    op.drop_table("ai_configs")
    op.drop_table("numbers")
    op.drop_table("user_companies")
    op.drop_table("users")
    op.drop_table("companies")
