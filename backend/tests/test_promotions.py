"""Promoções configuráveis — elegibilidade e transferência."""
import uuid
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from app.models import Conversation, Message, Promotion, Tool
from app.services.promotions import (
    get_active_promotions,
    promotion_transfer_active,
    send_promotion_to_client,
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


@pytest.mark.asyncio
async def test_send_promotion_banner_uses_plan_image_webhook(db_session, company):
    conv = Conversation(
        company_id=company.id,
        contact_phone="5593999999999",
        channel="whatsapp",
        status="open",
        ai_enabled=True,
    )
    db_session.add(conv)
    await db_session.flush()
    promo = Promotion(
        company_id=company.id,
        title="Outubro Rosa",
        message="50% na primeira mensalidade",
        image_url="https://exemplo.com/banner.webp",
        is_active=True,
    )
    db_session.add(promo)
    await db_session.commit()

    with patch(
        "app.services.message_flow._resolve_plan_images_tool",
        new_callable=AsyncMock,
        return_value=Tool(
            company_id=company.id,
            name="Planos",
            tool_key="enviar_imagens_planos",
            webhook_url="https://example.com/envia_imagem",
            is_active=True,
        ),
    ), patch(
        "app.services.message_flow._send_single_plan_image",
        new_callable=AsyncMock,
        return_value=True,
    ) as send_image_mock, patch(
        "app.services.message_flow.send_outbound",
        new_callable=AsyncMock,
    ) as outbound_mock:
        await send_promotion_to_client(
            db_session,
            conv,
            promo,
            unit_name="Santarém - Nova República",
        )

    send_image_mock.assert_awaited_once()
    image_payload = send_image_mock.await_args.args[2]
    assert image_payload["url"] == promo.image_url
    assert image_payload["unidade"] == "Santarém - Nova República"
    assert image_payload["plano"] == promo.title
    outbound_mock.assert_awaited_once()
    assert outbound_mock.await_args.args[3] == promo.message.strip()
    assert promo.image_url not in str(outbound_mock.await_args)
