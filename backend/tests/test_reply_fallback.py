"""A IA nunca pode deixar o cliente sem resposta (turno silencioso)."""
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio

from app.models import AiConfig, Conversation, Message
from app.security import encrypt_secret
from app.services.message_flow import reply_to_pending_messages


@pytest_asyncio.fixture
async def conversation_with_pending(db_session, company):
    config = AiConfig(
        company_id=company.id,
        ai_name="Mônica",
        llm_api_key_encrypted=encrypt_secret("sk-test"),
    )
    conv = Conversation(
        company_id=company.id,
        contact_phone="5593999887766",
        channel="test_console",
        status="open",
        ai_enabled=True,
    )
    db_session.add_all([config, conv])
    await db_session.flush()
    db_session.add(
        Message(
            conversation_id=conv.id,
            company_id=company.id,
            direction="inbound",
            actor="customer",
            content_type="text",
            text="Tem estacionamento?",
        )
    )
    await db_session.commit()
    return conv


@pytest.mark.asyncio
async def test_empty_ai_reply_still_sends_fallback_text(db_session, company, conversation_with_pending):
    conv = conversation_with_pending

    with patch(
        "app.services.message_flow.generate_ai_reply",
        new_callable=AsyncMock,
        return_value=("", None, False),
    ):
        result = await reply_to_pending_messages(db_session, conv.id, company.id)
        await db_session.commit()

    assert result is not None
    msgs = (
        await db_session.execute(
            __import__("sqlalchemy").select(Message).where(
                Message.conversation_id == conv.id, Message.actor == "ai"
            )
        )
    ).scalars().all()
    assert any("reformular" in (m.text or "").lower() for m in msgs)
