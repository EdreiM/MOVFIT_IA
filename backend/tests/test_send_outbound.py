"""Envio outbound para n8n/WTS."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import Conversation, Integration
from app.services.message_flow import _build_outbound_text_payload, send_outbound


def test_outbound_payload_includes_session_aliases():
    conv = Conversation(
        company_id=uuid.uuid4(),
        contact_phone="5593992219098",
        channel="whatsapp",
        status="open",
        external_conversation_id="sess-abc-123",
    )
    payload = _build_outbound_text_payload(conv, "Olá!")
    assert payload["sessionId"] == "sess-abc-123"
    assert payload["session_id"] == "sess-abc-123"
    assert payload["content"]["sessionId"] == "sess-abc-123"
    assert payload["text"] == "Olá!"


@pytest.mark.asyncio
async def test_send_outbound_logs_error_when_no_integration():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    conv = Conversation(
        company_id=uuid.uuid4(),
        contact_phone="559999",
        channel="whatsapp",
        status="open",
    )
    with patch(
        "app.services.message_flow._iter_outbound_integrations",
        new=AsyncMock(return_value=[]),
    ):
        await send_outbound(db, conv.company_id, conv, "teste")
    assert db.add.called
