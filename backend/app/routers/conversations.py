from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import NormalizedMessageEvent
from app.database import get_db
from app.deps import CurrentUser, get_current_user, require_roles, resolve_company_id
from app.models import Conversation, Message
from app.schemas import (
    AiStatusUpdate,
    ConversationOut,
    ConversationUpdate,
    MessageCreate,
    MessageOut,
)
from app.services.message_flow import save_message, send_outbound

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    status: str | None = None,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    stmt = select(Conversation).where(
        Conversation.company_id == company_id,
        Conversation.channel != "test_console",
    )
    if status:
        stmt = stmt.where(Conversation.status == status)
    stmt = stmt.order_by(Conversation.last_message_at.desc().nullslast())
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/{conversation_id}", response_model=ConversationOut)
async def get_conversation(
    conversation_id: UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    conv = await db.get(Conversation, conversation_id)
    if not conv or conv.company_id != company_id:
        raise HTTPException(status_code=404, detail="Conversa não encontrada")
    return conv


@router.patch("/{conversation_id}", response_model=ConversationOut)
async def update_conversation(
    conversation_id: UUID,
    payload: ConversationUpdate,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    conv = await db.get(Conversation, conversation_id)
    if not conv or conv.company_id != company_id:
        raise HTTPException(status_code=404, detail="Conversa não encontrada")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(conv, k, v)
    await db.flush()
    await db.refresh(conv)
    return conv


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: UUID,
    current: CurrentUser = Depends(require_roles("super_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Restrito a super_admin — pra apagar conversas de teste/dev sem deixar
    isso acessível pra atendentes/admins comuns da empresa."""
    company_id = await resolve_company_id(current, db)
    conv = await db.get(Conversation, conversation_id)
    if not conv or conv.company_id != company_id:
        raise HTTPException(status_code=404, detail="Conversa não encontrada")
    await db.delete(conv)


@router.patch("/{conversation_id}/ai-status", response_model=ConversationOut)
async def update_ai_status(
    conversation_id: UUID,
    payload: AiStatusUpdate,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    conv = await db.get(Conversation, conversation_id)
    if not conv or conv.company_id != company_id:
        raise HTTPException(status_code=404, detail="Conversa não encontrada")
    conv.ai_enabled = payload.ai_enabled
    if payload.ai_enabled and conv.status == "with_human":
        conv.status = "open"
    if not payload.ai_enabled:
        conv.status = "with_human"
    await db.flush()
    await db.refresh(conv)
    return conv


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
async def list_messages(
    conversation_id: UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    conv = await db.get(Conversation, conversation_id)
    if not conv or conv.company_id != company_id:
        raise HTTPException(status_code=404, detail="Conversa não encontrada")
    result = await db.execute(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at.asc())
    )
    return result.scalars().all()


@router.post("/{conversation_id}/messages", response_model=MessageOut)
async def send_manual_message(
    conversation_id: UUID,
    payload: MessageCreate,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    conv = await db.get(Conversation, conversation_id)
    if not conv or conv.company_id != company_id:
        raise HTTPException(status_code=404, detail="Conversa não encontrada")

    event = NormalizedMessageEvent(
        event_type="message_outbound",
        external_message_id=None,
        external_conversation_id=conv.external_conversation_id,
        channel_to=None,
        contact_phone=conv.contact_phone,
        content_type=payload.content_type,  # type: ignore[arg-type]
        text=payload.text,
        timestamp=datetime.now(timezone.utc),
        actor="human_agent",
        human_handoff_detected=True,
        raw_payload={"manual": True},
    )
    conv.ai_enabled = False
    conv.status = "with_human"
    msg = await save_message(db, conv, event)
    await send_outbound(db, company_id, conv, payload.text)
    await db.refresh(msg)
    return msg
