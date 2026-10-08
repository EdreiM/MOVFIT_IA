"""Encerramento automático por inatividade — limite de tentativas quando o
webhook de encerrar_atendimento falha."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.models import AiConfig, Conversation, Tool, ToolCallLog
from app.services.followup import MAX_CLOSE_ATTEMPTS, _close_conversation_due_to_inactivity
from app.services.message_flow import TOOL_KEY_END


@pytest_asyncio.fixture
async def conversation_with_end_tool(db_session, company):
    config = AiConfig(company_id=company.id, ai_name="Mônica")
    db_session.add(config)
    await db_session.flush()
    tool = Tool(
        company_id=company.id,
        ai_config_id=config.id,
        name="Encerrar atendimento",
        tool_key=TOOL_KEY_END,
        description="Encerra o atendimento",
        parameters=[],
        webhook_url="https://example.com/webhook/encerrar",
        is_active=True,
    )
    conv = Conversation(
        company_id=company.id,
        contact_phone="5593999887700",
        channel="whatsapp",
        status="open",
        ai_enabled=True,
        last_message_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db_session.add_all([tool, conv])
    await db_session.commit()
    await db_session.refresh(tool)
    await db_session.refresh(conv)
    return conv, tool


def _mock_webhook(client_cls, *, fail: bool) -> AsyncMock:
    client = AsyncMock()
    client.__aenter__.return_value = client
    if fail:
        client.post = AsyncMock(side_effect=RuntimeError("n8n fora do ar"))
    else:
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"sucesso": True, "mensagem": "Encerrado.", "dados": {}}
        client.post = AsyncMock(return_value=resp)
    client_cls.return_value = client
    return client


async def _failed_logs(db_session, conv) -> int:
    return await db_session.scalar(
        select(func.count())
        .select_from(ToolCallLog)
        .where(ToolCallLog.conversation_id == conv.id, ToolCallLog.success.is_(False))
    )


@pytest.mark.asyncio
async def test_close_gives_up_after_max_attempts(db_session, conversation_with_end_tool):
    conv, _ = conversation_with_end_tool

    with patch("app.services.message_flow.httpx.AsyncClient") as client_cls:
        client = _mock_webhook(client_cls, fail=True)

        for _ in range(MAX_CLOSE_ATTEMPTS - 1):
            await _close_conversation_due_to_inactivity(db_session, conv)
            await db_session.commit()
            assert conv.status == "open"

        await _close_conversation_due_to_inactivity(db_session, conv)
        await db_session.commit()

    assert conv.status == "resolved"
    assert client.post.await_count == MAX_CLOSE_ATTEMPTS
    assert await _failed_logs(db_session, conv) == MAX_CLOSE_ATTEMPTS


@pytest.mark.asyncio
async def test_close_ignores_failures_before_last_customer_message(db_session, conversation_with_end_tool):
    conv, tool = conversation_with_end_tool
    # Falhas de uma janela de silêncio anterior (antes de o cliente voltar a
    # falar) não contam pro limite da janela atual.
    db_session.add_all(
        [
            ToolCallLog(
                company_id=conv.company_id,
                conversation_id=conv.id,
                tool_id=tool.id,
                tool_key=tool.tool_key,
                tool_name=tool.name,
                success=False,
                arguments={},
                created_at=conv.last_message_at - timedelta(hours=2),
            )
            for _ in range(MAX_CLOSE_ATTEMPTS)
        ]
    )
    await db_session.commit()

    with patch("app.services.message_flow.httpx.AsyncClient") as client_cls:
        _mock_webhook(client_cls, fail=True)
        await _close_conversation_due_to_inactivity(db_session, conv)
        await db_session.commit()

    assert conv.status == "open"


@pytest.mark.asyncio
async def test_close_succeeds_on_first_attempt(db_session, conversation_with_end_tool):
    conv, _ = conversation_with_end_tool

    with patch("app.services.message_flow.httpx.AsyncClient") as client_cls:
        client = _mock_webhook(client_cls, fail=False)
        await _close_conversation_due_to_inactivity(db_session, conv)
        await db_session.commit()

    assert conv.status == "resolved"
    assert client.post.await_count == 1
    assert await _failed_logs(db_session, conv) == 0
