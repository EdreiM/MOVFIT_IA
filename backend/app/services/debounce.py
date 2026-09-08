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


async def _reply_after_silence(conversation_id: UUID, company_id: UUID, delay_seconds: float) -> None:
    try:
        await asyncio.sleep(delay_seconds)
    except asyncio.CancelledError:
        return

    # Import tardio: message_flow importa este módulo, então importar no topo
    # deste arquivo criaria um ciclo de import.
    from app.database import AsyncSessionLocal
    from app.services.locks import LOCK_NAMESPACE_CONVERSATION_REPLY, advisory_lock, uuid_lock_key
    from app.services.message_flow import reply_to_pending_messages

    async with AsyncSessionLocal() as db:
        try:
            # `_pending_replies` é em memória, por processo — se um dia
            # existir mais de uma cópia do backend rodando, cada uma tem seu
            # próprio dict e poderia disparar esse reply pra mesma conversa
            # ao mesmo tempo. O lock garante que só uma gera/manda de fato.
            async with advisory_lock(db, LOCK_NAMESPACE_CONVERSATION_REPLY, uuid_lock_key(conversation_id)) as acquired:
                if not acquired:
                    return
                await reply_to_pending_messages(db, conversation_id, company_id)
                await db.commit()
        except Exception:  # noqa: BLE001
            await db.rollback()
            logger.exception("Falha ao gerar resposta agregada da conversa %s", conversation_id)
        finally:
            _pending_replies.pop(conversation_id, None)
