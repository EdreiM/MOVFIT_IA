"""Aviso de fora do expediente depois de transferir pra atendente."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from app.models import AiConfig, Conversation, Message
from app.security import encrypt_secret
from app.services.message_flow import _outside_human_hours_notice, reply_to_pending_messages

BR = timezone(timedelta(hours=-3))


def _config(**overrides):
    base = dict(
        human_hours_enabled=True,
        human_hours_start="07:00",
        human_hours_end="22:00",
        human_hours_days="0,1,2,3,4,5",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_no_notice_inside_hours():
    # sexta 10h
    assert _outside_human_hours_notice(_config(), datetime(2026, 10, 9, 10, 0, tzinfo=BR)) is None


def test_notice_at_night():
    notice = _outside_human_hours_notice(_config(), datetime(2026, 10, 9, 23, 30, tzinfo=BR))
    assert notice is not None
    assert "07h" in notice and "22h" in notice
    assert "segunda a sábado" in notice


def test_notice_on_closed_day():
    # domingo 10h, mesmo dentro do intervalo de horas
    assert _outside_human_hours_notice(_config(), datetime(2026, 10, 11, 10, 0, tzinfo=BR))


def test_end_boundary_is_closed():
    assert _outside_human_hours_notice(_config(), datetime(2026, 10, 9, 22, 0, tzinfo=BR))
    assert _outside_human_hours_notice(_config(), datetime(2026, 10, 9, 7, 0, tzinfo=BR)) is None


def test_disabled_or_invalid_config_never_notifies():
    night = datetime(2026, 10, 9, 23, 30, tzinfo=BR)
    assert _outside_human_hours_notice(_config(human_hours_enabled=False), night) is None
    assert _outside_human_hours_notice(_config(human_hours_start="abc"), night) is None
    assert _outside_human_hours_notice(_config(human_hours_days=""), night) is None
    assert _outside_human_hours_notice(None, night) is None


@pytest_asyncio.fixture
async def convo(db_session, company):
    config = AiConfig(
        company_id=company.id,
        ai_name="Mônica",
        llm_api_key_encrypted=encrypt_secret("sk-test"),
        human_hours_enabled=True,
        human_hours_start="07:00",
        human_hours_end="22:00",
        human_hours_days="0,1,2,3,4,5,6",
    )
    conv = Conversation(
        company_id=company.id, contact_phone="5593999000222", channel="whatsapp",
        status="open", ai_enabled=True,
    )
    db_session.add_all([config, conv])
    await db_session.flush()
    db_session.add(
        Message(
            conversation_id=conv.id, company_id=company.id, direction="inbound",
            actor="customer", content_type="text", text="Quero falar com alguém",
        )
    )
    await db_session.commit()
    return conv


@pytest.mark.asyncio
async def test_notice_is_appended_only_when_transfer_succeeded(db_session, company, convo):
    async def fake_generate(db, conversation, text):
        conversation._transfer_succeeded_this_turn = True
        return "Pronto, passei seu caso pra um atendente.", None, False

    fixed_now = datetime(2026, 10, 9, 23, 0, tzinfo=BR)
    real_datetime = datetime

    class _FakeDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz else real_datetime.now()

    with patch("app.services.message_flow.generate_ai_reply", side_effect=fake_generate), patch(
        "app.services.message_flow.send_outbound", new_callable=AsyncMock
    ) as send, patch("app.services.message_flow.datetime", _FakeDatetime):
        await reply_to_pending_messages(db_session, convo.id, company.id)

    sent = " ".join(call.args[3] for call in send.await_args_list)
    assert "passei seu caso" in sent
    assert "fora do horário do atendimento humano" in sent
