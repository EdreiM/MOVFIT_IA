"""Lembrete da véspera + remarcar/cancelar avaliação física pelo chat."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models import Conversation, Lead, Message
from app.services import message_flow
from app.services.eval_reminders import build_reminder_text, run_eval_reminders
from app.services.message_flow import (
    TOOL_KEY_CANCEL_PHYSICAL_EVAL,
    TOOL_KEY_TRANSFER,
    _run_physical_eval_change_pipeline,
    _upcoming_physical_eval,
    _wants_physical_eval_change,
)

BR = timezone(timedelta(hours=-3))
PHONE = "5593999000555"


def _at(hour: int, minute: int = 0, days: int = 0) -> datetime:
    return datetime.now(BR).replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=days)


def _tomorrow() -> str:
    return (datetime.now(BR).date() + timedelta(days=1)).strftime("%Y%m%d")


@pytest_asyncio.fixture
async def lead(db_session, company):
    row = Lead(
        company_id=company.id, phone=PHONE, name="Maria Souza", cpf="12345678900",
        unit="Itaituba", stage="qualificado", physical_eval_scheduled=True,
        last_physical_eval_date=_tomorrow(), last_physical_eval_time="09:30",
    )
    db_session.add(row)
    await db_session.commit()
    return row


@pytest_asyncio.fixture
async def convo(db_session, company):
    conv = Conversation(
        company_id=company.id, contact_phone=PHONE, channel="whatsapp", status="resolved", ai_enabled=True,
    )
    db_session.add(conv)
    await db_session.commit()
    return conv


def test_reminder_text_mentions_day_time_unit_and_remarcar(lead):
    text = build_reminder_text(lead)
    assert "Maria" in text and "09:30" in text and "Itaituba" in text
    assert "remarcar" in text
    assert "avaliação física" in text


@pytest.mark.asyncio
async def test_reminder_sent_once_in_the_evening(db_session, lead, convo):
    with patch("app.services.eval_reminders.send_outbound", new_callable=AsyncMock) as send:
        assert await run_eval_reminders(db_session, now=_at(10)) == 0  # cedo demais
        assert await run_eval_reminders(db_session, now=_at(18, 30)) == 1
        assert await run_eval_reminders(db_session, now=_at(19, 0)) == 0  # já lembrou

    assert send.await_count == 1
    await db_session.refresh(lead)
    assert lead.physical_eval_reminder_for == _tomorrow()
    saved = (
        await db_session.execute(select(Message).where(Message.conversation_id == convo.id))
    ).scalars().all()
    assert len(saved) == 1
    assert saved[0].raw_payload["is_eval_reminder"] is True


@pytest.mark.asyncio
async def test_no_reminder_after_cutoff_or_for_other_days(db_session, company, lead, convo):
    with patch("app.services.eval_reminders.send_outbound", new_callable=AsyncMock) as send:
        assert await run_eval_reminders(db_session, now=_at(23, 0)) == 0
        lead.last_physical_eval_date = (datetime.now(BR).date() + timedelta(days=3)).strftime("%Y%m%d")
        await db_session.commit()
        assert await run_eval_reminders(db_session, now=_at(18, 30)) == 0
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_rescheduled_evaluation_gets_new_reminder(db_session, lead, convo):
    lead.physical_eval_reminder_for = "20200101"  # lembrete de uma data antiga
    await db_session.commit()
    with patch("app.services.eval_reminders.send_outbound", new_callable=AsyncMock):
        assert await run_eval_reminders(db_session, now=_at(18, 30)) == 1


@pytest.mark.asyncio
async def test_no_reminder_while_human_is_attending(db_session, lead, convo):
    convo.status = "with_human"
    await db_session.commit()
    with patch("app.services.eval_reminders.send_outbound", new_callable=AsyncMock) as send:
        assert await run_eval_reminders(db_session, now=_at(18, 30)) == 0
    send.assert_not_awaited()
    await db_session.refresh(lead)
    assert lead.physical_eval_reminder_for is None


def test_upcoming_eval_ignores_past_and_unscheduled():
    now = _at(12)
    today = now.strftime("%Y%m%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y%m%d")
    ok = SimpleNamespace(physical_eval_scheduled=True, last_physical_eval_date=today, last_physical_eval_time="15:00")
    past = SimpleNamespace(physical_eval_scheduled=True, last_physical_eval_date=yesterday, last_physical_eval_time="15:00")
    never = SimpleNamespace(physical_eval_scheduled=False, last_physical_eval_date=None, last_physical_eval_time=None)
    assert _upcoming_physical_eval(ok, now) == (today, "15:00")
    assert _upcoming_physical_eval(past, now) is None
    assert _upcoming_physical_eval(never, now) is None
    assert _upcoming_physical_eval(None, now) is None


def _msg(actor, text, payload=None):
    return SimpleNamespace(actor=actor, text=text, raw_payload=payload, created_at=datetime.now(timezone.utc))


def test_change_intent_requires_explicit_word_or_reminder_context():
    assert _wants_physical_eval_change("quero remarcar", [])
    assert _wants_physical_eval_change("preciso reagendar minha avaliação", [])
    assert _wants_physical_eval_change("quero cancelar a avaliação", [])
    # frase vaga sozinha não cancela nada
    assert not _wants_physical_eval_change("não vou conseguir ir", [_msg("ai", "Tudo certo!")])
    # mas como resposta ao lembrete da véspera, sim
    reminder = _msg("ai", "Passando pra lembrar...", {"is_eval_reminder": True})
    assert _wants_physical_eval_change("não vou conseguir ir", [reminder])
    # outro assunto
    assert not _wants_physical_eval_change("quero cancelar meu plano", [])
    assert not _wants_physical_eval_change("que horas vocês abrem?", [reminder])


@pytest.mark.asyncio
async def test_change_pipeline_cancels_and_reopens_scheduling(db_session, lead, convo):
    convo.physical_eval_offered_slots = {"date": lead.last_physical_eval_date, "slots": ["09:30"]}
    await db_session.commit()
    cancel_tool = SimpleNamespace(tool_key=TOOL_KEY_CANCEL_PHYSICAL_EVAL, webhook_url="http://n8n/cancel")
    fake = AsyncMock(return_value={"sucesso": True, "mensagem": "Cancelado", "dados": {}})

    with patch.object(message_flow, "execute_tool", fake):
        reply = await _run_physical_eval_change_pipeline(
            db_session, convo, {TOOL_KEY_CANCEL_PHYSICAL_EVAL: cancel_tool}, lead,
            (lead.last_physical_eval_date, "09:30"),
        )

    args = fake.await_args.args[2]
    assert args == {
        "cpf": "12345678900", "unidade": "Itaituba",
        "data": _tomorrow(), "horario": "09:30",
    }
    assert "cancelei" in reply and "avaliação física" in reply
    assert lead.last_physical_eval_date is None and lead.last_physical_eval_time is None
    assert convo.physical_eval_offered_slots is None


@pytest.mark.asyncio
async def test_change_pipeline_without_cancel_tool_transfers_to_reception(db_session, lead, convo):
    transfer = SimpleNamespace(tool_key=TOOL_KEY_TRANSFER, webhook_url="http://n8n/transfer")
    fake = AsyncMock(return_value={"sucesso": True, "mensagem": "ok", "dados": {}})

    with patch.object(message_flow, "execute_tool", fake):
        reply = await _run_physical_eval_change_pipeline(
            db_session, convo, {TOOL_KEY_TRANSFER: transfer}, lead,
            (lead.last_physical_eval_date, "09:30"),
        )

    assert fake.await_args.args[1] is transfer
    assert "remarcar" in fake.await_args.args[2]["motivo"]
    assert "recepção" in reply
    # a avaliação continua marcada até alguém remarcar de verdade
    assert lead.last_physical_eval_date == _tomorrow()


@pytest.mark.asyncio
async def test_change_pipeline_technical_failure_transfers_without_clearing(db_session, lead, convo):
    cancel_tool = SimpleNamespace(tool_key=TOOL_KEY_CANCEL_PHYSICAL_EVAL, webhook_url="http://n8n/cancel")
    transfer = SimpleNamespace(tool_key=TOOL_KEY_TRANSFER, webhook_url="http://n8n/transfer")

    async def fake_execute(db, tool, arguments, conversation):
        if tool is cancel_tool:
            return {"sucesso": False, "mensagem": "", "dados": {}}
        return {"sucesso": True, "mensagem": "ok", "dados": {}}

    with patch.object(message_flow, "execute_tool", side_effect=fake_execute):
        await _run_physical_eval_change_pipeline(
            db_session, convo,
            {TOOL_KEY_CANCEL_PHYSICAL_EVAL: cancel_tool, TOOL_KEY_TRANSFER: transfer},
            lead, (lead.last_physical_eval_date, "09:30"),
        )

    assert lead.last_physical_eval_date == _tomorrow()
