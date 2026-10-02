"""Promoções configuráveis — elegibilidade e transferência."""
import uuid
from datetime import date

import pytest

from app.models import Message, Promotion
from app.services.promotions import (
    get_active_promotions,
    promotion_transfer_active,
    wants_promotion_inquiry,
    woman_context_in_conversation,
)


@pytest.mark.asyncio
async def test_get_active_promotions_filters_by_date(db_session, company):
    promo = Promotion(
        company_id=company.id,
        title="Outubro Rosa",
        message="50% na primeira mensalidade",
        is_active=True,
        valid_from=date(2026, 10, 1),
        valid_until=date(2026, 10, 31),
        audience="women",
    )
    db_session.add(promo)
    await db_session.commit()

    inside = await get_active_promotions(db_session, company.id, today=date(2026, 10, 15))
    outside = await get_active_promotions(db_session, company.id, today=date(2026, 11, 1))

    assert len(inside) == 1
    assert len(outside) == 0


@pytest.mark.asyncio
async def test_get_active_promotions_respects_unit_filter(db_session, company):
    unit_a = uuid.uuid4()
    promo = Promotion(
        company_id=company.id,
        title="Promo local",
        message="Só uma unidade",
        is_active=True,
        unit_ids=[str(unit_a)],
    )
    db_session.add(promo)
    await db_session.commit()

    matched = await get_active_promotions(db_session, company.id, unit_id=unit_a)
    other = await get_active_promotions(db_session, company.id, unit_id=uuid.uuid4())

    assert len(matched) == 1
    assert len(other) == 0


def test_wants_promotion_inquiry():
    assert wants_promotion_inquiry("Tem alguma promoção esse mês?") is True
    assert wants_promotion_inquiry("Qual o horário?") is False


def test_woman_context_and_transfer_gate():
    assert woman_context_in_conversation("Sou mulher e quero a promo", []) is True
    assert woman_context_in_conversation("Quero sim", []) is False

    company_id = uuid.uuid4()
    conv_id = uuid.uuid4()
    promo_id = uuid.uuid4()
    promo = Promotion(
        id=promo_id,
        company_id=company_id,
        title="Outubro Rosa",
        message="50%",
        audience="women",
        requires_transfer=True,
    )
    history = [
        Message(
            company_id=company_id,
            conversation_id=conv_id,
            actor="ai",
            direction="outbound",
            text="Promo",
            raw_payload={"promotion_id": str(promo_id)},
        )
    ]
    assert promotion_transfer_active("Quero sim", history, ["sou mulher"], promo) is True
    assert promotion_transfer_active("Quero sim", history, [], promo) is False
