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
from app.services.locks import LOCK_NAMESPACE_FOLLOWUP_SWEEP, advisory_lock
from app.services.message_flow import (
    TOOL_KEY_CHECK_SESSION,
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

# Usadas quando a conversa ainda não teve nenhum assunto real discutido
# (só cumprimento) — ver _process_conversation. Fixas de propósito, pra
# nunca correr risco de a IA inventar um tema que nunca existiu.
_GENERIC_NUDGES = [
    "Oi! Ainda por aqui? Posso ajudar com alguma coisa? 😊",
    "Vou ficar por aqui caso precise de algo — é só chamar quando quiser! 👋",
]


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


async def _generate_followup_text(
    config,
    history: list[Message],
    attempt_number: int,
    max_attempts: int,
) -> str | None:
    previous_followups = [
        m.text
        for m in history
        if m.actor == "ai" and isinstance(m.raw_payload, dict) and m.raw_payload.get("is_followup") and m.text
    ]

    instruction = (
        "O cliente não responde há um tempo e a conversa ficou parada nesse ponto. "
        "Gere UMA mensagem CURTA (1-2 frases) pra retomar contato, relevante ao que "
        "estava sendo tratado. NÃO repita nenhuma informação que você já mandou antes "
        "(preço, lista de planos, link, horário, texto de qualquer tipo) — o cliente já "
        "recebeu tudo isso, só faça uma pergunta de acompanhamento sobre aquilo (ex: se "
        "mandou planos, pergunta se ficou alguma dúvida ou se quer ajuda pra escolher; "
        "se mandou link de pagamento, pergunta se conseguiu acessar; se só respondeu "
        "uma pergunta, pergunta se pode ajudar em mais alguma coisa). Não repita "
        "saudação genérica de início de conversa, não se desculpe por 'incomodar' ou "
        "'atrapalhar', e não invente nenhuma informação nova.\n\n"
        "REGRA CRÍTICA: só mencione algo que está LITERALMENTE escrito no histórico "
        "abaixo — nunca presuma que planos, links, opções ou qualquer outro material "
        "foram enviados se isso não aparecer explicitamente nas mensagens anteriores. "
        "Se não tiver certeza absoluta do que foi tratado, faça uma pergunta bem genérica "
        "(ex: 'ainda posso ajudar em algo?') em vez de arriscar inventar um assunto."
    )
    if previous_followups:
        instruction += (
            "\n\nATENÇÃO: essa é a tentativa de follow-up número " + str(attempt_number) + " nessa "
            "mesma conversa — você já mandou " + str(len(previous_followups)) + " antes, sem "
            "resposta do cliente. É PROIBIDO repetir a mesma frase ou pergunta de novo, mesmo "
            "parafraseada — varia completamente o ângulo (ex: se antes perguntou se ficou "
            "dúvida, agora pode perguntar se ainda tem interesse, oferecer ajuda de outro jeito, "
            "ou só confirmar se pode ajudar em algo mais). Textos que você JÁ MANDOU nos "
            "follow-ups anteriores dessa conversa (NÃO repita nada parecido com isso):\n"
            + "\n".join(f"- {t}" for t in previous_followups)
        )
    if attempt_number >= max_attempts:
        instruction += (
            "\n\nEssa é a ÚLTIMA tentativa — se o cliente não responder, o atendimento vai ser "
            "encerrado automaticamente por inatividade. Pode mencionar isso com naturalidade, "
            "sem soar como ameaça ou cobrança (ex: avisar que vai encerrar por aqui se não tiver "
            "retorno, deixando a porta aberta pra ele voltar quando quiser)."
        )

    messages = [
        {"role": "system", "content": compose_base_prompt(config)},
        {"role": "system", "content": instruction},
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
    logger.info(
        "Conversa %s encerrada automaticamente por inatividade (%s ferramenta encerrar_atendimento configurada).",
        conversation.id,
        "com" if end_tool and end_tool.webhook_url else "sem",
    )


async def _check_session_status(db, conversation: Conversation) -> tuple[bool, str | None]:
    """Se a empresa configurou a ferramenta opcional de verificar sessão
    (ex: consulta getSessionById no WTS), pergunta pra ela se a sessão do
    cliente ainda está pendente antes de mandar qualquer follow-up — evita
    reengajar um atendimento que já foi concluído OU assumido por um
    atendente humano por fora da Mônica (direto na outra plataforma, sem
    passar pela ferramenta transferir_atendimento). Sem essa ferramenta
    configurada, ou se a consulta falhar, assume que está pendente
    (comportamento de sempre — não trava o follow-up por uma falha técnica
    numa checagem opcional).

    Retorna (pendente, motivo) — motivo só importa quando pendente é False:
    "transferido" (humano assumiu por fora) ou "concluido"/None (encerrado
    sem passar por atendente) — usado pra decidir o status certo a
    registrar na conversa."""
    check_tool = await _find_tool(db, conversation, TOOL_KEY_CHECK_SESSION)
    if not check_tool or not check_tool.webhook_url:
        return True, None
    result = await execute_tool(db, check_tool, {}, conversation)
    if not result.get("sucesso"):
        return True, None
    dados = result.get("dados") if isinstance(result.get("dados"), dict) else {}
    pendente = bool(dados.get("pendente", True))
    motivo = dados.get("motivo") if isinstance(dados.get("motivo"), str) else None
    return pendente, motivo


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

    pendente, motivo = await _check_session_status(db, conversation)
    if not pendente:
        # Sessão já não está mais com a IA por fora da Mônica — sincroniza
        # aqui também e não manda follow-up nenhum, em vez de reengajar um
        # atendimento que já não é mais dela.
        if motivo == "transferido":
            conversation.ai_enabled = False
            conversation.status = "with_human"
            await _upsert_lead(
                db,
                conversation.company_id,
                conversation.contact_phone,
                {"estagio": "transferido"},
                sticky_flags={"was_transferred": True},
            )
            logger.info(
                "Conversa %s sincronizada como transferida — atendente assumiu na plataforma externa.",
                conversation.id,
            )
        else:
            conversation.status = "resolved"
            await _upsert_lead(db, conversation.company_id, conversation.contact_phone, {"estagio": "resolvido"})
            logger.info(
                "Conversa %s sincronizada como encerrada — sessão externa não está mais pendente.", conversation.id
            )
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

    ai_turns = [m for m in history if m.actor in {"ai", "human_agent"}]
    if len(ai_turns) <= 1:
        # Só existe a resposta inicial (ex: cliente disse "oi", IA respondeu
        # "como posso ajudar?") — não tem assunto real pra retomar ainda.
        # Gerar via LLM aqui arrisca inventar contexto que nunca existiu
        # (visto em produção: "você analisou as opções que enviei?" sem
        # nunca ter enviado nada) — usa mensagem fixa genérica em vez de
        # arriscar, e varia entre tentativas pelo mesmo motivo de sempre.
        text = _GENERIC_NUDGES[followups_sent % len(_GENERIC_NUDGES)]
    else:
        text = await _generate_followup_text(config, history, followups_sent + 1, config.followup_max_attempts)
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
    # Sessão dedicada só pra segurar o lock — se hoje só existe um worker
    # rodando, isso é um no-op; se um dia escalar pra múltiplas cópias do
    # backend, garante que só uma delas processa a varredura por vez (ver
    # CLAUDE.md, "Jobs em background").
    async with AsyncSessionLocal() as lock_db:
        async with advisory_lock(lock_db, LOCK_NAMESPACE_FOLLOWUP_SWEEP) as acquired:
            if not acquired:
                return
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
