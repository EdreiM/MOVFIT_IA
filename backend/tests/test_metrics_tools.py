"""Tabela "Uso por ferramenta" das métricas."""
from datetime import datetime, timedelta, timezone

import pytest

from app.models import Conversation, ToolCallLog
from app.routers.metrics import _compute_tools_stats
from app.services.message_flow import TOOL_KEY_CHECK_SESSION, TOOL_KEY_SEND_PLAN_IMAGES


async def _conversation(db_session, company, phone: str) -> Conversation:
    conv = Conversation(
        company_id=company.id, contact_phone=phone, channel="whatsapp", status="open", ai_enabled=True
    )
    db_session.add(conv)
    await db_session.flush()
    return conv


def _log(conv: Conversation, tool_key: str, *, success: bool, days_ago: int = 0) -> ToolCallLog:
    return ToolCallLog(
        company_id=conv.company_id,
        conversation_id=conv.id,
        tool_key=tool_key,
        tool_name=tool_key,
        success=success,
        arguments={},
        created_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )


@pytest.mark.asyncio
async def test_tools_stats_excludes_internal_session_check(db_session, company):
    conv = await _conversation(db_session, company, "5511999000101")
    db_session.add_all(
        [
            _log(conv, TOOL_KEY_CHECK_SESSION, success=False),
            _log(conv, TOOL_KEY_CHECK_SESSION, success=True),
            _log(conv, TOOL_KEY_SEND_PLAN_IMAGES, success=True),
        ]
    )
    await db_session.commit()

    stats = await _compute_tools_stats(db_session, company.id)

    assert [s.tool_key for s in stats] == [TOOL_KEY_SEND_PLAN_IMAGES]


@pytest.mark.asyncio
async def test_tools_stats_counts_successful_conversations_not_calls(db_session, company):
    conv_a = await _conversation(db_session, company, "5511999000102")
    conv_b = await _conversation(db_session, company, "5511999000103")
    db_session.add_all(
        [
            # Uma chamada por imagem: 3 chamadas na mesma conversa.
            _log(conv_a, TOOL_KEY_SEND_PLAN_IMAGES, success=True),
            _log(conv_a, TOOL_KEY_SEND_PLAN_IMAGES, success=True),
            _log(conv_a, TOOL_KEY_SEND_PLAN_IMAGES, success=True),
            _log(conv_b, TOOL_KEY_SEND_PLAN_IMAGES, success=False),
        ]
    )
    await db_session.commit()

    (stat,) = await _compute_tools_stats(db_session, company.id)

    assert stat.total_calls == 4
    assert stat.success_calls == 3
    assert stat.failed_calls == 1
    assert stat.distinct_conversations == 2
    assert stat.success_conversations == 1


@pytest.mark.asyncio
async def test_tools_stats_period_filter(db_session, company):
    conv = await _conversation(db_session, company, "5511999000104")
    db_session.add_all(
        [
            _log(conv, TOOL_KEY_SEND_PLAN_IMAGES, success=True, days_ago=1),
            _log(conv, TOOL_KEY_SEND_PLAN_IMAGES, success=False, days_ago=20),
        ]
    )
    await db_session.commit()

    (last_week,) = await _compute_tools_stats(db_session, company.id, days=7)
    (all_time,) = await _compute_tools_stats(db_session, company.id)

    assert (last_week.total_calls, last_week.failed_calls) == (1, 0)
    assert (all_time.total_calls, all_time.failed_calls) == (2, 1)
