"""Relatório operacional da IA — classificação e agregação."""
import uuid
from datetime import date, datetime, timezone

import pytest

from app.models import Conversation, Lead, Message, ToolCallLog, Unit
from app.services.message_flow import (
    TOOL_KEY_BOOK_PHYSICAL_EVAL,
    TOOL_KEY_SEND_PLAN_IMAGES,
    TOOL_KEY_TRANSFER,
)
from app.services.metrics_operations import (
    _classify_conversation_motive,
    _first_ai_response_seconds,
    _official_unit_label,
    compute_ai_operations_report,
)


def test_official_unit_label_merges_paraphrased_name():
    units = [
        Unit(company_id=uuid.uuid4(), name="Itaituba", city="Itaituba", unit_type="Prime"),
    ]
    assert _official_unit_label("MOVFIT Itaituba — Prime", units) == "Itaituba"
    assert _official_unit_label("Itaituba", units) == "Itaituba"


def test_classify_plans_motive():
    label = _classify_conversation_motive({TOOL_KEY_SEND_PLAN_IMAGES}, None)
    assert label == "Dúvidas sobre planos"


def test_classify_transfer_cancellation():
    label = _classify_conversation_motive({TOOL_KEY_TRANSFER}, "Cliente pediu cancelamento")
    assert label == "Solicitação de cancelamento"


def test_classify_physical_eval():
    label = _classify_conversation_motive({TOOL_KEY_BOOK_PHYSICAL_EVAL}, None)
    assert label == "Avaliação física"


def test_first_ai_response_seconds():
    import uuid

    conv_id = uuid.uuid4()
    t0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 9, 1, 12, 0, 45, tzinfo=timezone.utc)
    messages = [
        Message(
            company_id=uuid.uuid4(),
            conversation_id=conv_id,
            actor="customer",
            direction="inbound",
            text="Oi",
            created_at=t0,
        ),
        Message(
            company_id=uuid.uuid4(),
            conversation_id=conv_id,
            actor="ai",
            direction="outbound",
            text="Olá!",
            created_at=t1,
        ),
    ]
    assert _first_ai_response_seconds(messages) == pytest.approx(45.0)


@pytest.mark.asyncio
async def test_operations_report_empty_period(db_session, company):
    report = await compute_ai_operations_report(
        db_session,
        company.id,
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 30),
    )
    assert report.summary.conversations_total == 0
    assert report.summary.ai_resolution_rate is None
    assert len(report.daily_volume) == 30


@pytest.mark.asyncio
async def test_operations_report_counts_conversation_and_motive(db_session, company):
    created = datetime(2026, 9, 10, 15, 0, 0, tzinfo=timezone.utc)
    conv = Conversation(
        company_id=company.id,
        contact_phone="5511999887766",
        status="resolved",
        ai_enabled=True,
        channel="whatsapp",
        created_at=created,
    )
    db_session.add(conv)
    await db_session.flush()

    db_session.add(
        Message(
            company_id=company.id,
            conversation_id=conv.id,
            actor="customer",
            direction="inbound",
            text="Quero planos",
            created_at=created,
        )
    )
    db_session.add(
        Message(
            company_id=company.id,
            conversation_id=conv.id,
            actor="ai",
            direction="outbound",
            text="Segue",
            created_at=datetime(2026, 9, 10, 15, 1, 0, tzinfo=timezone.utc),
        )
    )
    db_session.add(
        ToolCallLog(
            company_id=company.id,
            conversation_id=conv.id,
            tool_key=TOOL_KEY_SEND_PLAN_IMAGES,
            tool_name="Enviar planos",
            success=True,
            arguments={},
        )
    )
    await db_session.commit()

    report = await compute_ai_operations_report(
        db_session,
        company.id,
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 30),
    )

    assert report.summary.conversations_total == 1
    assert report.summary.plans_presented == 1
    assert report.summary.ai_resolved == 1
    assert report.motivations[0].label == "Dúvidas sobre planos"
    assert report.response_times.samples == 1
    assert report.response_times.median_seconds == pytest.approx(60.0)


@pytest.mark.asyncio
async def test_operations_report_merges_unit_aliases(db_session, company):
    unit = Unit(company_id=company.id, name="Itaituba", city="Itaituba", unit_type="Prime")
    db_session.add(unit)
    await db_session.flush()

    created = datetime(2026, 9, 11, 10, 0, 0, tzinfo=timezone.utc)
    conv_a = Conversation(
        company_id=company.id,
        contact_phone="5511999000001",
        status="open",
        ai_enabled=True,
        channel="whatsapp",
        created_at=created,
    )
    conv_b = Conversation(
        company_id=company.id,
        contact_phone="5511999000002",
        status="open",
        ai_enabled=True,
        channel="whatsapp",
        created_at=created,
    )
    db_session.add_all([conv_a, conv_b])
    await db_session.flush()

    db_session.add_all(
        [
            Lead(company_id=company.id, phone="5511999000001", unit="Itaituba"),
            Lead(company_id=company.id, phone="5511999000002", unit="MOVFIT Itaituba — Prime"),
        ]
    )
    await db_session.commit()

    report = await compute_ai_operations_report(
        db_session,
        company.id,
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 30),
    )

    itaituba_rows = [row for row in report.units if row.unit == "Itaituba"]
    assert len(itaituba_rows) == 1
    assert itaituba_rows[0].conversations == 2
