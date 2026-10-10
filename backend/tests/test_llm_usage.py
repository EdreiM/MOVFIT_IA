"""Custo da IA: captura de tokens por chamada, cálculo do custo e os números
do painel (app/services/llm_usage.py)."""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.models import AiConfig, Conversation, Lead, LlmUsage, Message
from app.security import encrypt_secret
from app.services.llm import chat_completion
from app.services.llm_usage import collect_llm_usage, compute_ai_cost, cost_usd
from app.services.message_flow import reply_to_pending_messages


def test_cost_uses_cached_and_output_prices():
    # 800k de entrada normal + 200k em cache + 100k de saída no gpt-4o-mini.
    assert cost_usd("gpt-4o-mini", 1_000_000, 200_000, 100_000) == Decimal("0.195")
    assert cost_usd("gpt-4.1-mini", 1_000_000, 0, 1_000_000) == Decimal("2.00")


def test_cost_matches_dated_model_names_to_the_right_price():
    # A API devolve o nome com data; "gpt-4o-mini-..." não pode cair no preço do "gpt-4o".
    assert cost_usd("gpt-4o-mini-2024-07-18", 1_000_000, 0, 0) == Decimal("0.15")
    assert cost_usd("gpt-4o-2024-08-06", 1_000_000, 0, 0) == Decimal("2.50")
    assert cost_usd("modelo-desconhecido", 1000, 0, 1000) is None


def _openai_response(prompt_tokens: int, completion_tokens: int, cached: int = 0, text: str = "Olá!"):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "model": "gpt-4o-mini-2024-07-18",
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "prompt_tokens_details": {"cached_tokens": cached},
        },
    }
    return response


def _patched_openai(*responses):
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post = AsyncMock(side_effect=list(responses))
    return patch("app.services.llm.httpx.AsyncClient", return_value=client)


@pytest.mark.asyncio
async def test_usage_is_collected_even_through_a_nested_task():
    async def call():
        return await chat_completion(provider="openai", model="gpt-4o-mini", api_key="sk", messages=[])

    with _patched_openai(_openai_response(1200, 80, cached=1000), _openai_response(300, 20)):
        with collect_llm_usage() as usage:
            await call()
            # reply_to_pending_messages chama o gerador dentro de asyncio.wait_for (outra task).
            await asyncio.wait_for(call(), timeout=5)
        # Fora do bloco nada é anotado — e não pode dar erro.
        assert usage == [
            {"model": "gpt-4o-mini-2024-07-18", "prompt_tokens": 1200, "cached_tokens": 1000, "completion_tokens": 80},
            {"model": "gpt-4o-mini-2024-07-18", "prompt_tokens": 300, "cached_tokens": 0, "completion_tokens": 20},
        ]


@pytest.mark.asyncio
async def test_reply_records_the_tokens_and_cost_of_the_turn(db_session, company):
    config = AiConfig(company_id=company.id, ai_name="Mônica", llm_api_key_encrypted=encrypt_secret("sk-test"))
    conv = Conversation(
        company_id=company.id, contact_phone="5593999887744", channel="whatsapp", status="open", ai_enabled=True
    )
    db_session.add_all([config, conv])
    await db_session.flush()
    db_session.add(
        Message(
            company_id=company.id, conversation_id=conv.id, direction="inbound", actor="customer",
            content_type="text", text="oi", created_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()

    with patch(
        "app.services.message_flow.fetch_rag_context", new_callable=AsyncMock, return_value=""
    ), patch("app.services.message_flow.send_outbound", new_callable=AsyncMock), _patched_openai(
        _openai_response(2000, 100, text="Olá! Como posso ajudar?")
    ):
        reply = await reply_to_pending_messages(db_session, conv.id, company.id)
        await db_session.commit()

    assert reply == "Olá! Como posso ajudar?"
    (row,) = (await db_session.execute(select(LlmUsage))).scalars().all()
    assert (row.conversation_id, row.purpose, row.model) == (conv.id, "resposta", "gpt-4o-mini-2024-07-18")
    assert (row.prompt_tokens, row.completion_tokens) == (2000, 100)
    # 2000 × 0,15 + 100 × 0,60, por milhão de tokens.
    assert row.cost_usd == Decimal("0.00036")


@pytest.mark.asyncio
async def test_ai_cost_by_month_and_by_customer(db_session, company):
    ana = Conversation(company_id=company.id, contact_phone="+55 93 99988-0001", contact_name="Ana", channel="whatsapp")
    ana_again = Conversation(company_id=company.id, contact_phone="+55 93 99988-0001", channel="whatsapp")
    bia = Conversation(company_id=company.id, contact_phone="5593999880002", channel="whatsapp")
    test_chat = Conversation(company_id=company.id, contact_phone="__test_console__", channel="test_console")
    db_session.add_all([ana, ana_again, bia, test_chat, Lead(company_id=company.id, phone="5593999880002", name="Bia Souza")])
    await db_session.flush()

    def usage(conv, usd: str, when: datetime, model: str = "gpt-4o-mini") -> LlmUsage:
        return LlmUsage(
            company_id=company.id, conversation_id=conv.id, model=model, purpose="resposta",
            prompt_tokens=1000, completion_tokens=100, cost_usd=Decimal(usd) if usd else None, created_at=when,
        )

    september = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)
    october = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    # 01/10 01:00 UTC ainda é 30/09 em Brasília: conta em setembro.
    brasilia_edge = datetime(2026, 10, 1, 1, tzinfo=timezone.utc)
    db_session.add_all(
        [
            usage(ana, "0.10", october),
            usage(ana_again, "0.30", october),
            usage(bia, "0.20", october),
            usage(bia, "", october, model="modelo-sem-preco"),
            usage(test_chat, "0.50", october),
            usage(ana, "1.00", september),
            usage(ana, "2.00", brasilia_edge),
        ]
    )
    await db_session.commit()

    cost = await compute_ai_cost(db_session, company.id, month="2026-10", usd_brl_rate=5.0)

    assert cost["total"] == {"usd": 1.1, "brl": 5.5}
    assert cost["test_chat"] == {"usd": 0.5, "brl": 2.5}
    assert cost["calls"] == 5 and cost["calls_without_price"] == 1
    # Por cliente: mesmo telefone soma as conversas; Chat de teste fica fora.
    assert [(c["name"], c["usd"], c["brl"], c["calls"]) for c in cost["customers"]] == [
        ("Ana", 0.4, 2.0, 2),
        ("Bia Souza", 0.2, 1.0, 2),
    ]
    assert cost["customers_count"] == 2
    assert cost["average_per_customer"] == {"usd": 0.3, "brl": 1.5}
    assert [(m["month"], m["usd"]) for m in cost["by_month"]] == [("2026-09", 3.0), ("2026-10", 1.1)]

    everything = await compute_ai_cost(db_session, company.id, month=None, usd_brl_rate=5.2)
    assert everything["total"] == {"usd": 4.1, "brl": 21.32}
    assert everything["customers"][0]["usd"] == 3.4
