from __future__ import annotations

import asyncio
import logging
from uuid import UUID

logger = logging.getLogger(__name__)

# Timers em memória do processo, por conversa. Cada mensagem nova cancela o
# timer anterior e recomeça a contagem — só responde depois que o cliente
# fica em silêncio por `delay_seconds`, agregando as mensagens da rajada.
_pending_replies: dict[UUID, asyncio.Task] = {}


def schedule_ai_reply(conversation_id: UUID, company_id: UUID, delay_seconds: float) -> None:
    existing = _pending_replies.get(conversation_id)
    if existing and not existing.done():
        existing.cancel()
    _pending_replies[conversation_id] = asyncio.create_task(
        _reply_after_silence(conversation_id, company_id, delay_seconds)
    )


async def _try_reply_once(conversation_id: UUID, company_id: UUID) -> bool:
    """Tenta gerar a resposta agregada. Retorna True se rodou, False se o lock
    estava ocupado (outro turno ainda processando RAG/LLM/envio de bolhas)."""
    from app.database import AsyncSessionLocal
    from app.services.locks import LOCK_NAMESPACE_CONVERSATION_REPLY, advisory_lock, uuid_lock_key
    from app.services.message_flow import reply_to_pending_messages

    async with AsyncSessionLocal() as db:
        try:
            async with advisory_lock(
                db, LOCK_NAMESPACE_CONVERSATION_REPLY, uuid_lock_key(conversation_id)
            ) as acquired:
                if not acquired:
                    return False
                await reply_to_pending_messages(db, conversation_id, company_id)
                await db.commit()
                return True
        except Exception:  # noqa: BLE001
            await db.rollback()
            logger.exception("Falha ao gerar resposta agregada da conversa %s", conversation_id)
            return True  # erro já logado — não ficar retentando o mesmo turno
    return False


async def _reply_after_silence(conversation_id: UUID, company_id: UUID, delay_seconds: float) -> None:
    try:
        await asyncio.sleep(delay_seconds)
    except asyncio.CancelledError:
        return

    # Import tardio: message_flow importa este módulo, então importar no topo
    # deste arquivo criaria um ciclo de import.
    max_lock_attempts = 8
    for attempt in range(max_lock_attempts):
        if await _try_reply_once(conversation_id, company_id):
            break
        wait = min(2.0 * (attempt + 1), 12.0)
        logger.warning(
            "Resposta da conversa %s adiada — outro turno ainda em processamento "
            "(tentativa %s/%s, nova tentativa em %.0fs)",
            conversation_id,
            attempt + 1,
            max_lock_attempts,
            wait,
        )
        try:
            await asyncio.sleep(wait)
        except asyncio.CancelledError:
            return
    else:
        logger.error(
            "Conversa %s ficou sem resposta automática — lock ocupado após %s tentativas",
            conversation_id,
            max_lock_attempts,
        )
    _pending_replies.pop(conversation_id, None)
