"""Loja interna — vestuário e acessórios: informar unidade e transferir."""
from unittest.mock import AsyncMock, patch

import pytest

from app.models import AiConfig, Conversation, Tool
from app.services.message_flow import (
    TOOL_KEY_TRANSFER,
    _internal_store_product_active,
    _run_internal_store_transfer_pipeline,
    _text_mentions_internal_store_product,
)


def test_detects_jacket_price_question():
    text = "Quanto esta a jaqueta drifit masculina preta?"
    assert _text_mentions_internal_store_product(text) is True
    assert _internal_store_product_active(text) is True


def test_ignores_gym_plans_question():
    text = "Quanto custa o plano mensal?"
    assert _text_mentions_internal_store_product(text) is False


def test_detects_short_followup_after_store_question():
    assert _internal_store_product_active(
        "Qual unidade?",
        recent_customer_texts=["Quero comprar uma camiseta dry fit"],
    ) is True


def test_does_not_hijack_when_topic_changes_to_plans():
    assert _internal_store_product_active(
        "Quero ver os planos da unidade",
        recent_customer_texts=["Quanto custa a jaqueta?"],
    ) is False


@pytest.mark.asyncio
async def test_internal_store_pipeline_transfers(db_session, company):
    conv = Conversation(
        company_id=company.id,
        contact_phone="5511999000099",
        status="open",
        ai_enabled=True,
        channel="whatsapp",
    )
    config = AiConfig(company_id=company.id)
    db_session.add(config)
    db_session.add(conv)
    await db_session.flush()

    transfer_tool = Tool(
        company_id=company.id,
        ai_config_id=config.id,
        tool_key=TOOL_KEY_TRANSFER,
        name="Transferir",
        webhook_url="https://example.com/transfer",
        is_active=True,
    )
    db_session.add(transfer_tool)
    await db_session.commit()

    tools_by_key = {TOOL_KEY_TRANSFER: transfer_tool}
    with patch(
        "app.services.message_flow.execute_tool",
        new_callable=AsyncMock,
        return_value={"sucesso": True, "mensagem": "ok", "dados": {}},
    ) as execute_mock:
        reply = await _run_internal_store_transfer_pipeline(db_session, conv, tools_by_key)

    assert reply
    assert "loja interna" in reply.lower()
    assert "estoque" in reply.lower()
    execute_mock.assert_awaited_once()
    args = execute_mock.await_args.args
    assert "loja interna" in args[2]["motivo"].lower()
