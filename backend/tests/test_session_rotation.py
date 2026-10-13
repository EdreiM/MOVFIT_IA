"""Cliente que volta depois da sessão encerrada: a conversa segue a sessão atual."""
from datetime import datetime, timezone

import pytest

from app.adapters.base import NormalizedMessageEvent
from app.models import Conversation
from app.services.message_flow import get_or_create_conversation

PHONE = "5593991544807"


def _event(session_id: str | None, actor: str = "customer") -> NormalizedMessageEvent:
    return NormalizedMessageEvent(
        event_type="message_inbound",
        external_message_id=None,
        external_conversation_id=session_id,
        channel_to=None,
        contact_phone=PHONE,
        content_type="text",
        text="Bom dia",
        timestamp=datetime.now(timezone.utc),
        actor=actor,
    )


@pytest.mark.asyncio
async def test_conversation_follows_the_new_session_of_a_returning_customer(db_session, company):
    first = await get_or_create_conversation(db_session, company.id, _event("sessao-antiga"))
    await db_session.commit()
    assert first.external_conversation_id == "sessao-antiga"

    # a sessão antiga foi encerrada; o cliente escreve de novo → sessão nova, mesmo telefone
    again = await get_or_create_conversation(db_session, company.id, _event("sessao-nova"))
    await db_session.commit()

    assert again.id == first.id  # continua sendo a mesma conversa (histórico preservado)
    assert again.external_conversation_id == "sessao-nova"  # transferir/consultar usa a sessão viva


@pytest.mark.asyncio
async def test_same_session_and_missing_session_do_not_change_anything(db_session, company):
    conv = await get_or_create_conversation(db_session, company.id, _event("sessao-1"))
    await db_session.commit()

    same = await get_or_create_conversation(db_session, company.id, _event("sessao-1"))
    assert same.id == conv.id and same.external_conversation_id == "sessao-1"

    # evento sem id de sessão (ex.: outro canal) nunca apaga o id guardado
    blank = await get_or_create_conversation(db_session, company.id, _event(None))
    assert blank.id == conv.id and blank.external_conversation_id == "sessao-1"


@pytest.mark.asyncio
async def test_resolved_conversation_still_starts_a_fresh_one(db_session, company):
    old = await get_or_create_conversation(db_session, company.id, _event("sessao-a"))
    old.status = "resolved"
    await db_session.commit()

    fresh = await get_or_create_conversation(db_session, company.id, _event("sessao-b"))
    await db_session.commit()

    assert fresh.id != old.id
    assert fresh.external_conversation_id == "sessao-b"
    assert (await db_session.get(Conversation, old.id)).external_conversation_id == "sessao-a"
