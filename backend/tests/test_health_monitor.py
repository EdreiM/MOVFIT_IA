"""Alertas de saúde e relatório diário (health_monitor)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models import Conversation, KnowledgeGap, Message, SystemEvent, ToolCallLog
from app.services import health_monitor
from app.services.health_monitor import run_health_checks

BR = timezone(timedelta(hours=-3))


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest_asyncio.fixture
async def convo(db_session, company):
    conv = Conversation(
        company_id=company.id, contact_phone="5593999000333", channel="whatsapp",
        status="open", ai_enabled=True,
    )
    db_session.add(conv)
    await db_session.commit()
    return conv


async def _add_calls(db, company, conv, tool_key, ok, fail, error="HTTP 500", at=None):
    at = at or _now() - timedelta(minutes=5)
    for index in range(ok + fail):
        db.add(
            ToolCallLog(
                company_id=company.id, conversation_id=conv.id, tool_key=tool_key,
                tool_name="Transferir pra atendente", success=index >= fail,
                arguments={}, error_message=error if index < fail else None, created_at=at,
            )
        )
    await db.commit()


async def _alerts(db, company):
    result = await db.execute(
        select(SystemEvent).where(SystemEvent.company_id == company.id, SystemEvent.kind == "alert")
    )
    return result.scalars().all()


@pytest.mark.asyncio
async def test_failing_tool_opens_alert_once_and_resolves(db_session, company, convo):
    await _add_calls(db_session, company, convo, "transferir_atendimento", ok=1, fail=5)

    await run_health_checks(db_session)
    await run_health_checks(db_session)  # segunda varredura não duplica

    alerts = await _alerts(db_session, company)
    assert len(alerts) == 1
    assert alerts[0].key == "tool_failing:transferir_atendimento"
    assert "5 de 6" in alerts[0].body and "HTTP 500" in alerts[0].body
    assert alerts[0].resolved_at is None

    # janela passou: as falhas saem dos últimos 30 min → alerta fecha sozinho
    await run_health_checks(db_session, now=_now() + timedelta(hours=2))
    await db_session.refresh(alerts[0])
    assert alerts[0].resolved_at is not None


@pytest.mark.asyncio
async def test_few_calls_or_low_failure_rate_do_not_alert(db_session, company, convo):
    await _add_calls(db_session, company, convo, "consultar_parcelas", ok=0, fail=3)  # poucas chamadas
    await _add_calls(db_session, company, convo, "enviar_imagens_planos", ok=8, fail=2)  # 20%
    await run_health_checks(db_session)
    assert await _alerts(db_session, company) == []


@pytest.mark.asyncio
async def test_check_session_failures_are_ignored(db_session, company, convo):
    await _add_calls(db_session, company, convo, "verificar_sessao_atendimento", ok=0, fail=10)
    await run_health_checks(db_session)
    assert await _alerts(db_session, company) == []


@pytest.mark.asyncio
async def test_dismissed_alert_is_not_recreated_while_condition_lasts(db_session, company, convo):
    await _add_calls(db_session, company, convo, "transferir_atendimento", ok=0, fail=6)
    await run_health_checks(db_session)
    (alert,) = await _alerts(db_session, company)
    alert.dismissed_at = _now()
    await db_session.commit()

    await run_health_checks(db_session)
    assert len(await _alerts(db_session, company)) == 1


@pytest.mark.asyncio
async def test_pending_transfer_alert(db_session, company, convo):
    convo.transfer_pending_at = _now() - timedelta(minutes=20)
    convo.transfer_pending_reason = "Cliente quer cancelar"
    await db_session.commit()

    await run_health_checks(db_session)

    (alert,) = await _alerts(db_session, company)
    assert alert.key == "transfer_pending"
    assert alert.severity == "critical"


@pytest.mark.asyncio
async def test_recent_pending_transfer_is_not_alerted_yet(db_session, company, convo):
    convo.transfer_pending_at = _now() - timedelta(minutes=2)
    await db_session.commit()
    await run_health_checks(db_session)
    assert await _alerts(db_session, company) == []


@pytest.mark.asyncio
async def test_unanswered_needs_at_least_two_conversations(db_session, company, convo):
    last = _now() - timedelta(minutes=30)
    convo.last_message_at = last
    db_session.add(
        Message(conversation_id=convo.id, company_id=company.id, direction="inbound",
                actor="customer", content_type="text", text="oi", created_at=last)
    )
    await db_session.commit()

    await run_health_checks(db_session)
    assert await _alerts(db_session, company) == []  # só uma conversa

    second = Conversation(
        company_id=company.id, contact_phone="5593999000444", channel="whatsapp",
        status="open", ai_enabled=True, last_message_at=last,
    )
    db_session.add(second)
    await db_session.flush()
    db_session.add(
        Message(conversation_id=second.id, company_id=company.id, direction="inbound",
                actor="customer", content_type="text", text="alguém aí?", created_at=last)
    )
    await db_session.commit()

    await run_health_checks(db_session)
    (alert,) = await _alerts(db_session, company)
    assert alert.key == "unanswered"
    assert "2 clientes" in alert.title


@pytest.mark.asyncio
async def test_answered_conversation_is_not_unanswered(db_session, company, convo):
    last = _now() - timedelta(minutes=30)
    convo.last_message_at = last
    db_session.add_all(
        [
            Message(conversation_id=convo.id, company_id=company.id, direction="inbound",
                    actor="customer", content_type="text", text="oi", created_at=last),
            Message(conversation_id=convo.id, company_id=company.id, direction="outbound",
                    actor="ai", content_type="text", text="oi!", created_at=last + timedelta(seconds=20)),
        ]
    )
    await db_session.commit()
    conditions = await health_monitor._unanswered(db_session, company.id, _now())
    assert conditions == []
    # mesmo com o limite mínimo, a conversa respondida nem entra na contagem
    count = await db_session.scalar(
        select(Conversation.id).where(health_monitor._unanswered_filter(company.id, _now()))
    )
    assert count is None


@pytest.mark.asyncio
async def test_daily_report_is_created_once_after_report_hour(db_session, company, convo):
    yesterday_noon = datetime.now(BR).replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=1)
    convo.created_at = yesterday_noon
    db_session.add(
        KnowledgeGap(
            company_id=company.id, conversation_id=convo.id,
            question="Vocês têm piscina aquecida?", ai_reply="Não tenho essa informação",
            reason="sem_informacao", created_at=yesterday_noon,
        )
    )
    await db_session.commit()
    await _add_calls(db_session, company, convo, "transferir_atendimento", ok=1, fail=2, at=yesterday_noon)

    morning = datetime.now(BR).replace(hour=8, minute=0, second=0, microsecond=0)
    await run_health_checks(db_session, now=morning)
    await run_health_checks(db_session, now=morning + timedelta(minutes=5))

    reports = (
        await db_session.execute(select(SystemEvent).where(SystemEvent.kind == "report"))
    ).scalars().all()
    assert len(reports) == 1
    body = reports[0].body
    assert "Conversas novas: 1" in body
    assert "2 de 3 chamadas falharam" in body
    assert "piscina aquecida" in body
    assert reports[0].key == f"daily:{(morning.date() - timedelta(days=1)).isoformat()}"


@pytest.mark.asyncio
async def test_daily_report_waits_for_report_hour(db_session, company):
    early = datetime.now(BR).replace(hour=3, minute=0, second=0, microsecond=0)
    await run_health_checks(db_session, now=early)
    reports = (
        await db_session.execute(select(SystemEvent).where(SystemEvent.kind == "report"))
    ).scalars().all()
    assert reports == []


@pytest.mark.asyncio
async def test_webhook_is_notified_only_when_alert_opens(db_session, company, convo):
    await _add_calls(db_session, company, convo, "transferir_atendimento", ok=0, fail=6)
    with patch.object(health_monitor, "_notify", new_callable=AsyncMock) as notify:
        await run_health_checks(db_session)
        await run_health_checks(db_session)
    assert notify.await_count == 1
    assert notify.await_args.args[0] == "alert"


@pytest.mark.asyncio
async def test_events_endpoints(client, db_session, company, user):
    db_session.add_all(
        [
            SystemEvent(company_id=company.id, kind="alert", severity="critical",
                        key="transfer_pending", title="Transferência parada", body="..."),
            SystemEvent(company_id=company.id, kind="report", severity="info",
                        key="daily:2026-10-09", title="Relatório de 09/10/2026", body="..."),
        ]
    )
    await db_session.commit()

    login = await client.post(
        "/auth/login", json={"email": "teste@movfit.com", "password": "senha-teste-123"}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    count = await client.get("/admin/events/active-count", headers=headers)
    assert count.json() == {"count": 1}

    alerts = await client.get("/admin/events?kind=alert&active=true", headers=headers)
    assert [e["key"] for e in alerts.json()] == ["transfer_pending"]

    dismissed = await client.post(f"/admin/events/{alerts.json()[0]['id']}/dismiss", headers=headers)
    assert dismissed.status_code == 200
    assert dismissed.json()["dismissed_at"] is not None

    assert (await client.get("/admin/events/active-count", headers=headers)).json() == {"count": 0}
    reports = await client.get("/admin/events?kind=report", headers=headers)
    assert len(reports.json()) == 1
