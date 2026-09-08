import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from app.adapters.base import NormalizedMessageEvent
from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models import Conversation, Message, Tool
from app.security import decrypt_secret
from app.services.llm import chat_completion
from app.services.message_flow import (
    TOOL_KEY_END,
    _upsert_lead,
    compose_base_prompt,
    execute_tool,
    resolve_ai_config,
    save_message,
    send_outbound,
    split_into_bubbles,
)

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 300  # varredura a cada 5 minutos — a precisão do
# horário do follow-up não precisa ser fina, é medida em minutos/horas


async def _find_tool(db, conversation: Conversation, tool_key: str) -> Tool | None:
    """Mesmo critério de resolução usado em generate_ai_reply: ferramenta
    escopada pra essa integração vence sobre uma global de mesma chave."""
    result = await db.execute(
        select(Tool).where(
            Tool.company_id == conversation.company_id,
            Tool.tool_key == tool_key,
            Tool.is_active.is_(True),
            or_(Tool.integration_id.is_(None), Tool.integration_id == conversation.integration_id),
        )
    )
    tools = result.scalars().all()
    if not tools:
        return None
    tools.sort(key=lambda t: t.integration_id is not None)
    return tools[-1]


async def _generate_followup_text(config, history: list[Message]) -> str | None:
    messages = [
        {"role": "system", "content": compose_base_prompt(config)},
        {
            "role": "system",
            "content": (
                "O cliente não responde há um tempo e a conversa ficou parada nesse ponto. "
                "Gere UMA mensagem CURTA (1-2 frases) pra retomar contato, relevante ao que "
                "estava sendo tratado. NÃO repita nenhuma informação que você já mandou antes "
                "(preço, lista de planos, link, horário, texto de qualquer tipo) — o cliente já "
                "recebeu tudo isso, só faça uma pergunta de acompanhamento sobre aquilo (ex: se "
                "mandou planos, pergunta se ficou alguma dúvida ou se quer ajuda pra escolher; "
                "se mandou link de pagamento, pergunta se conseguiu acessar; se só respondeu "
                "uma pergunta, pergunta se pode ajudar em mais alguma coisa). Não repita "
                "saudação genérica de início de conversa, não se desculpe por 'incomodar' ou "
                "'atrapalhar', e não invente nenhuma informação nova."
            ),
        },
    ]
    for m in history:
        role = "assistant" if m.actor in {"ai", "human_agent"} else "user"
        if m.text:
            messages.append({"role": role, "content": m.text})

    api_key = decrypt_secret(config.llm_api_key_encrypted)
    assistant_message = await chat_completion(
        provider=config.llm_provider,
        model=config.llm_model,
        api_key=api_key,
        messages=messages,
        temperature=config.temperature,
    )
    return (assistant_message.get("content") or "").strip() or None


async def _close_conversation_due_to_inactivity(db, conversation: Conversation) -> None:
    """Mesmo destino de quando a própria IA chama encerrar_atendimento —
    chama o webhook configurado (se existir) pra manter o sistema externo
    ciente, ou só marca localmente se a empresa ainda não configurou essa
    ferramenta."""
    end_tool = await _find_tool(db, conversation, TOOL_KEY_END)
    if end_tool and end_tool.webhook_url:
        await execute_tool(
            db,
            end_tool,
            {"motivo": "Cliente inativo após follow-up(s) sem resposta — atendimento encerrado automaticamente."},
            conversation,
        )
    else:
        conversation.status = "resolved"
        await _upsert_lead(db, conversation.company_id, conversation.contact_phone, {"estagio": "resolvido"})


async def _process_conversation(db, conversation: Conversation) -> None:
    config = await resolve_ai_config(db, conversation)
    if not config or not config.followup_enabled or not config.llm_api_key_encrypted:
        return

    history_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc())
        .limit(20)
    )
    history = list(reversed(history_result.scalars().all()))
    if not history:
        return

    last_msg = history[-1]
    if last_msg.actor != "ai":
        # Cliente falou por último (ainda não respondemos — não é follow-up,
        # é o fluxo normal de debounce) ou humano assumiu — não mexe.
        return

    now = datetime.now(timezone.utc)
    if now - last_msg.created_at < timedelta(minutes=config.followup_delay_minutes):
        return

    # last_message_at só é atualizado quando o CLIENTE manda mensagem (ver
    # get_or_create_conversation) — então isso marca o início da janela de
    # silêncio atual, mesmo depois de um ou mais follow-ups já enviados.
    last_customer_at = conversation.last_message_at
    followups_sent = sum(
        1
        for m in history
        if m.actor == "ai"
        and m.created_at > last_customer_at
        and isinstance(m.raw_payload, dict)
        and m.raw_payload.get("is_followup")
    )
    if followups_sent >= config.followup_max_attempts:
        await _close_conversation_due_to_inactivity(db, conversation)
        return

    text = await _generate_followup_text(config, history)
    if not text:
        return

    settings = get_settings()
    bubbles = split_into_bubbles(text, settings.ai_bubble_max_chars)
    for bubble_text in bubbles:
        out_event = NormalizedMessageEvent(
            event_type="message_outbound",
            external_message_id=None,
            external_conversation_id=conversation.external_conversation_id,
            channel_to=None,
            contact_phone=conversation.contact_phone,
            content_type="text",
            text=bubble_text,
            timestamp=datetime.now(timezone.utc),
            actor="ai",
            raw_payload={"is_followup": True, "attempt": followups_sent + 1},
        )
        await save_message(db, conversation, out_event)
        await send_outbound(db, conversation.company_id, conversation, bubble_text)
    logger.info("Follow-up #%s enviado pra conversa %s", followups_sent + 1, conversation.id)


async def run_followup_sweep() -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Conversation).where(
                Conversation.status == "open",
                Conversation.ai_enabled.is_(True),
                Conversation.channel != "test_console",
            )
        )
        conversations = result.scalars().all()
        for conversation in conversations:
            try:
                await _process_conversation(db, conversation)
                await db.commit()
            except Exception:  # noqa: BLE001
                logger.exception("Falha ao processar follow-up da conversa %s", conversation.id)
                await db.rollback()


async def periodic_followup_loop() -> None:
    """Roda pra sempre em background (ver lifespan em app/main.py) — uma
    exceção numa varredura não deve derrubar as próximas."""
    while True:
        try:
            await run_followup_sweep()
        except Exception:  # noqa: BLE001
            logger.exception("Falha na varredura de follow-up")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
