"""Cálculo de métricas overview — taxa de resolução pela IA."""
import uuid

import pytest

from app.models import Conversation
from app.routers.metrics import _compute_overview


@pytest.mark.asyncio
async def test_ai_resolution_rate_counts_transfers_in_denominator(db_session, company):
    conv_ai = Conversation(
        company_id=company.id,
        contact_phone="5511999000001",
        status="resolved",
        ai_enabled=True,
        channel="whatsapp",
    )
    conv_human = Conversation(
        company_id=company.id,
        contact_phone="5511999000002",
        status="with_human",
        ai_enabled=False,
        channel="whatsapp",
    )
    conv_open = Conversation(
        company_id=company.id,
        contact_phone="5511999000003",
        status="open",
        ai_enabled=True,
        channel="whatsapp",
    )
    db_session.add_all([conv_ai, conv_human, conv_open])
    await db_session.commit()

    overview = await _compute_overview(db_session, company.id)

    assert overview.ai_resolved == 1
    assert overview.with_human_total == 1
    assert overview.ai_resolution_rate == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_ai_resolution_rate_none_without_outcomes(db_session, company):
    conv_open = Conversation(
        company_id=company.id,
        contact_phone="5511999000004",
        status="open",
        ai_enabled=True,
        channel="whatsapp",
    )
    db_session.add(conv_open)
    await db_session.commit()

    overview = await _compute_overview(db_session, company.id)

    assert overview.ai_resolution_rate is None
