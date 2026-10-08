"""Modo vendedor: planos explicados em conversa (sem legenda/frases prontas),
arte do plano só como apoio. Com a chave desligada o fluxo de catálogo de
sempre tem que continuar idêntico."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models import AiConfig, Conversation, Message, Plan, Promotion, Tool, Unit
from app.security import encrypt_secret
from app.services.followup import _generate_followup_text
from app.services.message_flow import (
    TOOL_KEY_SEND_PLAN_IMAGES,
    TOOL_KEY_TRANSFER,
    _plan_tour_transfer_active,
    _present_plan_art_sales_mode,
    _resolve_plan_images,
    _single_plan_mentioned,
    compose_base_prompt,
    generate_ai_reply,
)
from app.services.promotions import confirms_promotion_interest, sales_promotion_to_transfer

UNIT_NAME = "Santarém - 24 horas"
SELLER_REPLY = (
    "Pra quem quer treinar o ano todo eu iria no Plano Anual Parcelado, que sai por R$ 197,00 "
    "por mês.\n\nQuer que eu te mande o link pra garantir?"
)


async def _setup(db_session, company, *, sales_mode: bool):
    unit = Unit(company_id=company.id, name=UNIT_NAME, city="Santarém", unit_type="Premium")
    db_session.add(unit)
    await db_session.flush()
    db_session.add_all(
        [
            Plan(
                company_id=company.id,
                unit_id=unit.id,
                name="Plano Anual Parcelado",
                monthly_price=197.0,
                fidelity_months=12,
                benefits=["Acesso 24 horas"],
                image_url="https://exemplo.com/anual.png",
                signup_url="https://exemplo.com/matricula/anual",
            ),
            Plan(
                company_id=company.id,
                unit_id=unit.id,
                name="Plano Mensal Recorrente",
                monthly_price=217.0,
                image_url="https://exemplo.com/mensal.png",
                signup_url="https://exemplo.com/matricula/mensal",
            ),
        ]
    )
    config = AiConfig(
        company_id=company.id,
        ai_name="Mônica",
        llm_api_key_encrypted=encrypt_secret("sk-test"),
        sales_mode_enabled=sales_mode,
    )
    db_session.add(config)
    await db_session.flush()
    tool = Tool(
        company_id=company.id,
        ai_config_id=config.id,
        name="Imagens planos",
        tool_key=TOOL_KEY_SEND_PLAN_IMAGES,
        description="Envia imagens",
        parameters=[
            {"name": "Unidade", "type": "string", "required": True},
            {"name": "Nome do Plano", "type": "string", "required": False},
        ],
        webhook_url="https://example.com/hook",
        is_active=True,
    )
    conv = Conversation(
        company_id=company.id,
        contact_phone="5593999887711",
        channel="test_console",
        status="open",
        ai_enabled=True,
    )
    db_session.add_all([tool, conv])
    await db_session.commit()
    return config, tool, conv


@pytest_asyncio.fixture
async def sales_setup(db_session, company):
    return await _setup(db_session, company, sales_mode=True)


async def _ai_messages(db_session, conv) -> list[Message]:
    result = await db_session.execute(
        select(Message).where(Message.conversation_id == conv.id, Message.actor == "ai")
    )
    return list(result.scalars().all())


def _system_text(llm_mock) -> str:
    messages = llm_mock.await_args_list[0].kwargs["messages"]
    return "\n".join(m["content"] for m in messages if m["role"] == "system")


async def _ask_for_plans(db_session, conv, llm_side_effect):
    with patch(
        "app.services.message_flow.fetch_rag_context", new_callable=AsyncMock, return_value=""
    ), patch(
        "app.services.message_flow.chat_completion", new_callable=AsyncMock, side_effect=llm_side_effect
    ) as llm_mock:
        result = await generate_ai_reply(db_session, conv, f"Quero saber os planos de {UNIT_NAME}")
        await db_session.commit()
    return result, llm_mock


@pytest.mark.asyncio
async def test_sales_mode_keeps_ai_text_and_sends_no_template(db_session, sales_setup):
    _, _, conv = sales_setup

    (reply, _, delivered), llm_mock = await _ask_for_plans(
        db_session, conv, [{"content": SELLER_REPLY, "tool_calls": None}]
    )

    # O texto da IA é a apresentação: sai como ela escreveu, sem fechamento fixo de tour.
    assert reply == SELLER_REPLY
    # A IA recomendou um plano sem chamar a ferramenta: a rede de segurança manda
    # só a arte desse plano — sem introdução, sem legenda e sem os outros planos.
    assert delivered is True
    ai_messages = await _ai_messages(db_session, conv)
    assert [m.content_type for m in ai_messages] == ["image"]
    assert ai_messages[0].raw_payload["images"][0]["plano"] == "Plano Anual Parcelado"

    system = _system_text(llm_mock)
    assert "[Planos — modo vendedor]" in system
    assert "R$ 197,00" in system and "https://exemplo.com/matricula/anual" in system
    assert "NOME DO PLANO EM MAIÚSCULAS" not in system
    assert "fora do assunto planos/matrícula, só responda o que foi perguntado" in system


@pytest.mark.asyncio
async def test_sales_mode_finds_unit_from_ai_reply_when_customer_abbreviates(db_session, sales_setup):
    """Cliente escreve "24h": o código não reconhece a unidade por esse texto,
    mas a IA responde com o nome cadastrado — a arte tem que ir mesmo assim."""
    _, _, conv = sales_setup
    llm_reply = {
        "content": f"Na unidade *{UNIT_NAME}* eu indicaria o Plano Mensal Recorrente, por R$ 217,00 por mês.",
        "tool_calls": None,
    }

    with patch(
        "app.services.message_flow.fetch_rag_context", new_callable=AsyncMock, return_value=""
    ), patch("app.services.message_flow.chat_completion", new_callable=AsyncMock, return_value=llm_reply):
        await generate_ai_reply(db_session, conv, "24h")
        await db_session.commit()

    ai_messages = await _ai_messages(db_session, conv)
    assert [m.content_type for m in ai_messages] == ["image"]
    assert ai_messages[0].raw_payload["images"][0]["plano"] == "Plano Mensal Recorrente"


@pytest.mark.asyncio
async def test_sales_mode_sends_no_art_when_reply_compares_plans(db_session, sales_setup):
    _, _, conv = sales_setup
    comparison = (
        "Lá tem o Plano Anual Parcelado por R$ 197,00 e o Plano Mensal Recorrente por R$ 217,00 "
        "por mês.\n\nVocê pretende treinar o ano todo?"
    )

    (reply, _, delivered), _ = await _ask_for_plans(
        db_session, conv, [{"content": comparison, "tool_calls": None}]
    )

    assert reply == comparison
    assert delivered is False
    assert await _ai_messages(db_session, conv) == []


@pytest.mark.asyncio
async def test_sales_mode_off_keeps_catalog_flow(db_session, company):
    _, _, conv = await _setup(db_session, company, sales_mode=False)

    (_, _, delivered), llm_mock = await _ask_for_plans(
        db_session, conv, [{"content": "Claro!", "tool_calls": None}]
    )

    system = _system_text(llm_mock)
    assert "[Planos — modo vendedor]" not in system
    assert "NOME DO PLANO EM MAIÚSCULAS" in system
    # Rede de segurança de sempre: introdução + imagem e legenda de cada plano.
    ai_messages = await _ai_messages(db_session, conv)
    assert delivered is True
    assert any(m.text == f"Aqui estão os planos da unidade {UNIT_NAME}:" for m in ai_messages)
    assert sum(1 for m in ai_messages if m.content_type == "image") == 2
    assert sum(1 for m in ai_messages if (m.raw_payload or {}).get("plan_caption")) == 2


@pytest.mark.asyncio
async def test_sales_mode_tool_sends_only_the_recommended_plan_art(db_session, sales_setup):
    _, _, conv = sales_setup
    tool_call = {
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "function": {
                    "name": TOOL_KEY_SEND_PLAN_IMAGES,
                    "arguments": json.dumps(
                        {"Unidade": UNIT_NAME, "Nome do Plano": "Plano Anual Parcelado"}
                    ),
                },
            }
        ],
    }

    (reply, _, delivered), _ = await _ask_for_plans(
        db_session, conv, [tool_call, {"content": SELLER_REPLY, "tool_calls": None}]
    )

    assert reply == SELLER_REPLY
    assert delivered is True
    ai_messages = await _ai_messages(db_session, conv)
    assert [m.content_type for m in ai_messages] == ["image"]
    image = ai_messages[0].raw_payload["images"][0]
    assert image["plano"] == "Plano Anual Parcelado"
    assert "legenda" not in image


@pytest.mark.asyncio
async def test_sales_mode_refuses_all_plans_unless_customer_asked_for_images(db_session, sales_setup):
    _, tool, conv = sales_setup
    all_images = await _resolve_plan_images(db_session, conv.company_id, {"Unidade": UNIT_NAME})
    assert len(all_images) == 2

    touched: set[str] = set()
    refused = await _present_plan_art_sales_mode(
        db_session, conv, tool, all_images, explicit_request=False, touched_units=touched
    )
    assert refused["sucesso"] is False
    assert touched == set()
    assert await _ai_messages(db_session, conv) == []

    sent = await _present_plan_art_sales_mode(
        db_session, conv, tool, all_images, explicit_request=True, touched_units=touched
    )
    assert sent["sucesso"] is True
    ai_messages = await _ai_messages(db_session, conv)
    assert [m.content_type for m in ai_messages] == ["image", "image"]


@pytest.mark.asyncio
async def test_sales_mode_converts_markdown_bold_to_whatsapp_bold(db_session, sales_setup):
    _, _, conv = sales_setup

    (reply, _, _), _ = await _ask_for_plans(
        db_session,
        conv,
        [{"content": "O **Plano Anual Parcelado** sai por *R$ 197,00* por mês.", "tool_calls": None}],
    )

    assert reply == "O *Plano Anual Parcelado* sai por *R$ 197,00* por mês."


def test_single_plan_mentioned_needs_one_clear_plan():
    anual = Plan(name="Plano Anual Parcelado", image_url="https://exemplo.com/anual.png")
    mensal = Plan(name="Plano Mensal Recorrente", image_url="https://exemplo.com/mensal.png")
    avulso = Plan(name="Mensal Avulso")
    plans = [anual, mensal, avulso]

    # A IA quase nunca repete o nome cadastrado inteiro.
    assert _single_plan_mentioned("Pra vocês eu iria no plano anual, R$ 197,00 por mês.", plans) is anual
    assert _single_plan_mentioned("O mensal avulso sai por R$ 250,00.", plans) is avulso
    # Comparação entre planos, ou resposta que nem fala de plano/valor: nenhum.
    assert _single_plan_mentioned("Tem o plano anual por R$ 197 e o mensal por R$ 217.", plans) is None
    assert _single_plan_mentioned("A unidade abre às 6h e o estacionamento é gratuito.", plans) is None
    assert _single_plan_mentioned("A renovação anual é automática.", plans) is None


def _msg(actor: str, text: str, minutes_ago: int) -> Message:
    return Message(
        actor=actor,
        direction="inbound" if actor == "customer" else "outbound",
        content_type="text",
        text=text,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )


def test_sales_mode_yes_only_confirms_tour_when_it_was_the_last_offer():
    history = [
        _msg("customer", "quero ver os planos", 10),
        _msg("ai", "Se quiser conhecer antes, posso agendar um tour pela academia.", 9),
        _msg("customer", "depois eu vejo isso, quanto é o anual?", 8),
        _msg("ai", "O anual sai por R$ 197,00 por mês. Posso te mandar o link pra garantir?", 7),
        _msg("customer", "sim", 0),
    ]
    customer_texts = [m.text for m in history if m.actor == "customer"]

    # Fluxo de catálogo: tour discutido em qualquer ponto + "sim" = transferir pro tour.
    assert _plan_tour_transfer_active("sim", history, customer_texts) is True
    # Modo vendedor: o "sim" responde à pergunta do link, não ao tour de antes.
    assert _plan_tour_transfer_active("sim", history, customer_texts, require_recent_offer=True) is False

    history[3] = _msg("ai", "Que tal um tour pra conhecer a academia antes de decidir?", 7)
    assert _plan_tour_transfer_active("sim", history, customer_texts, require_recent_offer=True) is True
    # Pedido explícito de tour continua valendo de qualquer jeito.
    assert _plan_tour_transfer_active("quero um tour", history[:2], [], require_recent_offer=True) is True


def test_base_prompt_scope_rule_depends_on_sales_mode():
    assert compose_base_prompt(AiConfig(sales_mode_enabled=False)).endswith(
        "isso não faz parte do seu atendimento; só responda o que foi perguntado."
    )
    assert "fora do assunto planos/matrícula" in compose_base_prompt(AiConfig(sales_mode_enabled=True))


@pytest.mark.asyncio
@pytest.mark.parametrize("sales_mode", [True, False])
async def test_followup_gets_sales_instruction_only_in_sales_mode(sales_mode):
    config = AiConfig(
        ai_name="Mônica", llm_api_key_encrypted=encrypt_secret("sk-test"), sales_mode_enabled=sales_mode
    )
    history = [
        _msg("customer", "quanto é o plano anual?", 200),
        _msg("ai", "O anual sai por R$ 197,00 por mês.", 199),
        _msg("ai", "Posso te mandar o link?", 198),
    ]
    with patch(
        "app.services.followup.chat_completion",
        new_callable=AsyncMock,
        return_value={"content": "Oi! Ficou alguma dúvida sobre o anual?"},
    ) as llm_mock:
        await _generate_followup_text(config, history, 1, 2)

    system = "\n".join(
        m["content"] for m in llm_mock.await_args.kwargs["messages"] if m["role"] == "system"
    )
    assert ("MODO VENDEDOR" in system) is sales_mode


# --- Promoções no modo vendedor ------------------------------------------

PROMO_TITLE = "Outubro Rosa — 50% na 1ª mensalidade"
PROMO_TEMPLATE = "✨ OUTUBRO É MÊS DE SE CUIDAR! 💪💗 Novas alunas ganham 50% de desconto na primeira mensalidade."
PROMO_PITCH = (
    "Lá o Plano Anual Parcelado sai por R$ 197,00 por mês — e nesse mês tem o Outubro Rosa: "
    "novas alunas pagam metade da primeira mensalidade.\n\n"
    "Quer aproveitar essa condição? Te passo pra um atendente garantir."
)


async def _add_promotion(db_session, company, **overrides) -> Promotion:
    promotion = Promotion(
        company_id=company.id,
        title=PROMO_TITLE,
        message=PROMO_TEMPLATE,
        image_url="https://exemplo.com/outubro-rosa.png",
        audience="women",
        requires_transfer=True,
        mention_on_plan_request=True,
        **overrides,
    )
    db_session.add(promotion)
    await db_session.commit()
    return promotion


@pytest.mark.asyncio
async def test_sales_mode_sends_promo_banner_without_official_text(db_session, company, sales_setup):
    _, _, conv = sales_setup
    promotion = await _add_promotion(db_session, company)

    (reply, _, _), llm_mock = await _ask_for_plans(
        db_session, conv, [{"content": PROMO_PITCH, "tool_calls": None}]
    )

    # Quem apresenta a promoção é o texto da IA; o texto oficial não é enviado.
    assert reply == PROMO_PITCH
    ai_messages = await _ai_messages(db_session, conv)
    assert all(m.text != PROMO_TEMPLATE for m in ai_messages)
    assert [m.content_type for m in ai_messages] == ["image", "image"]  # arte do plano + banner
    banner = next(m for m in ai_messages if (m.raw_payload or {}).get("promotion_banner"))
    assert banner.raw_payload["promotion_id"] == str(promotion.id)
    # A IA recebe a promoção como contexto, com a instrução de falar do jeito dela.
    system = _system_text(llm_mock)
    assert "[Promoções ativas" in system and "NUNCA copie o texto oficial" in system


@pytest.mark.asyncio
async def test_sales_mode_sends_no_promo_art_when_reply_does_not_mention_it(db_session, company, sales_setup):
    _, _, conv = sales_setup
    await _add_promotion(db_session, company)

    await _ask_for_plans(db_session, conv, [{"content": SELLER_REPLY, "tool_calls": None}])

    ai_messages = await _ai_messages(db_session, conv)
    assert not any((m.raw_payload or {}).get("promotion_id") for m in ai_messages)


def test_promotion_confirmation_is_not_triggered_by_questions():
    assert confirms_promotion_interest("sim") is True
    assert confirms_promotion_interest("quero aproveitar sim") is True
    assert confirms_promotion_interest("pode ser") is True
    # No fluxo de catálogo "essa" já contava como confirmação.
    assert confirms_promotion_interest("essa promoção vale pra qual plano?") is False
    assert confirms_promotion_interest("quero saber mais sobre isso") is False
    assert confirms_promotion_interest("e como funciona o treino grátis") is False


def test_sales_promotion_transfer_needs_confirmation_of_the_promotion_itself():
    promotion = Promotion(title=PROMO_TITLE, message=PROMO_TEMPLATE, requires_transfer=True, trigger_keywords=[])
    offer = [
        _msg("customer", "queria ver os planos", 5),
        _msg("ai", "O anual sai por R$ 197,00 — e tem o Outubro Rosa, com 50% na primeira mensalidade.", 4),
        _msg("ai", "Quer aproveitar essa condição? Te passo pra um atendente garantir.", 4),
    ]

    assert sales_promotion_to_transfer("sim", offer + [_msg("customer", "sim", 0)], [promotion]) is promotion
    # Dúvida não transfere: a IA continua atendendo.
    question = "essa promoção vale pro plano mensal?"
    assert sales_promotion_to_transfer(question, offer + [_msg("customer", question, 0)], [promotion]) is None

    # A última pergunta da IA era sobre o link, não sobre a promoção: "sim" não transfere.
    link_offer = offer[:2] + [_msg("ai", "Posso te mandar o link pra garantir a vaga?", 4)]
    assert sales_promotion_to_transfer("sim", link_offer + [_msg("customer", "sim", 0)], [promotion]) is None
    # Mas se o cliente pedir a promoção com as próprias palavras, transfere.
    assert sales_promotion_to_transfer("quero o desconto", link_offer, [promotion]) is promotion


@pytest.mark.asyncio
async def test_sales_mode_transfers_when_customer_confirms_promotion(db_session, company, sales_setup):
    config, _, conv = sales_setup
    promotion = await _add_promotion(db_session, company)
    transfer_tool = Tool(
        company_id=company.id,
        ai_config_id=config.id,
        name="Transferir atendimento",
        tool_key=TOOL_KEY_TRANSFER,
        description="Transfere pra um atendente",
        parameters=[{"name": "motivo", "type": "string", "required": True}],
        webhook_url="https://example.com/transferir",
        is_active=True,
    )
    db_session.add(transfer_tool)
    for actor, text_ in (("customer", "queria ver os planos"), ("ai", PROMO_PITCH), ("customer", "quero sim")):
        db_session.add(
            Message(
                company_id=company.id,
                conversation_id=conv.id,
                direction="inbound" if actor == "customer" else "outbound",
                actor=actor,
                content_type="text",
                text=text_,
            )
        )
        await db_session.flush()
    await db_session.commit()

    with patch(
        "app.services.message_flow.fetch_rag_context", new_callable=AsyncMock, return_value=""
    ), patch("app.services.message_flow.chat_completion", new_callable=AsyncMock) as llm_mock:
        reply, _, _ = await generate_ai_reply(db_session, conv, "quero sim")
        await db_session.commit()

    llm_mock.assert_not_awaited()  # transferência determinística, sem depender do modelo
    assert promotion.title in reply
    assert conv.status == "with_human" and conv.ai_enabled is False
