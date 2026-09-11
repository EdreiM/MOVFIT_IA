from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.adapters.base import NormalizedMessageEvent
from app.database import get_db
from app.deps import CurrentUser, get_current_user, resolve_company_id
from app.models import AiConfig, Conversation, Message, RagSource, Tool
from app.schemas import (
    AiConfigOut,
    AiConfigUpdate,
    MessageOut,
    RagSourceCreate,
    RagSourceOut,
    RagSourceUpdate,
    TestChatRequest,
    TestChatResponse,
    ToolCreate,
    ToolOut,
    ToolUpdate,
)
from app.config import get_settings
from app.security import decrypt_secret, encrypt_secret, mask_api_key
from app.services.debounce import schedule_ai_reply
from app.services.message_flow import save_message

router = APIRouter(tags=["ai-configs"])

TEST_CHAT_PHONE = "__test_console__"


def _to_out(config: AiConfig) -> AiConfigOut:
    masked = None
    has_key = bool(config.llm_api_key_encrypted)
    if has_key:
        try:
            masked = mask_api_key(decrypt_secret(config.llm_api_key_encrypted))
        except Exception:  # noqa: BLE001
            masked = "****"
    return AiConfigOut(
        id=config.id,
        company_id=config.company_id,
        integration_id=config.integration_id,
        ai_name=config.ai_name,
        tone=config.tone,
        use_emoji=config.use_emoji,
        system_prompt=config.system_prompt,
        llm_provider=config.llm_provider,
        llm_model=config.llm_model,
        llm_api_key_masked=masked,
        has_api_key=has_key,
        temperature=config.temperature,
        operation_mode=config.operation_mode,
        followup_enabled=config.followup_enabled,
        followup_delay_minutes=config.followup_delay_minutes,
        followup_max_attempts=config.followup_max_attempts,
    )


async def _get_or_create_integration_config(
    db: AsyncSession, company_id: UUID, integration_id: UUID, default_config: AiConfig
) -> AiConfig:
    """Configuração específica de uma integração — se ainda não existir,
    nasce como cópia integral da configuração padrão da empresa, pra você
    já começar personalizando a partir do que já existe, em vez de um
    formulário em branco."""
    result = await db.execute(
        select(AiConfig).where(AiConfig.company_id == company_id, AiConfig.integration_id == integration_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        config = AiConfig(
            company_id=company_id,
            integration_id=integration_id,
            ai_name=default_config.ai_name,
            tone=default_config.tone,
            use_emoji=default_config.use_emoji,
            system_prompt=default_config.system_prompt,
            llm_provider=default_config.llm_provider,
            llm_model=default_config.llm_model,
            llm_api_key_encrypted=default_config.llm_api_key_encrypted,
            temperature=default_config.temperature,
            operation_mode=default_config.operation_mode,
            followup_enabled=default_config.followup_enabled,
            followup_delay_minutes=default_config.followup_delay_minutes,
            followup_max_attempts=default_config.followup_max_attempts,
        )
        db.add(config)
        await db.flush()
    return config


async def _get_default_ai_config(db: AsyncSession, company_id: UUID) -> AiConfig:
    """A configuração "padrão" da empresa (integration_id nulo) — usada como
    base pra novas personalizações por integração, e como dona de RAGs e
    ferramentas (que continuam da empresa como um todo, não de uma
    personalização específica)."""
    result = await db.execute(
        select(AiConfig).where(AiConfig.company_id == company_id, AiConfig.integration_id.is_(None))
    )
    config = result.scalar_one_or_none()
    if not config:
        config = AiConfig(company_id=company_id)
        db.add(config)
        await db.flush()
    return config


@router.get("/ai-configs/{company_id}", response_model=AiConfigOut)
async def get_ai_config(
    company_id: UUID,
    integration_id: UUID | None = Query(None),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    resolved = await resolve_company_id(current, db)
    if company_id != resolved and not current.is_super_admin:
        raise HTTPException(status_code=403, detail="Sem acesso")

    default_config = await _get_default_ai_config(db, company_id)
    if integration_id is None:
        return _to_out(default_config)

    config = await _get_or_create_integration_config(db, company_id, integration_id, default_config)
    return _to_out(config)


@router.patch("/ai-configs/{company_id}", response_model=AiConfigOut)
async def update_ai_config(
    company_id: UUID,
    payload: AiConfigUpdate,
    integration_id: UUID | None = Query(None),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    resolved = await resolve_company_id(current, db)
    if company_id != resolved and not current.is_super_admin:
        raise HTTPException(status_code=403, detail="Sem acesso")

    if integration_id is None:
        config = await _get_default_ai_config(db, company_id)
    else:
        default_config = await _get_default_ai_config(db, company_id)
        config = await _get_or_create_integration_config(db, company_id, integration_id, default_config)

    data = payload.model_dump(exclude_unset=True)
    api_key = data.pop("llm_api_key", None)
    for k, v in data.items():
        setattr(config, k, v)
    if api_key:
        config.llm_api_key_encrypted = encrypt_secret(api_key)
    await db.flush()
    await db.refresh(config)
    return _to_out(config)


@router.post("/ai-configs/{company_id}/rag-sources", response_model=RagSourceOut)
async def create_rag_source(
    company_id: UUID,
    payload: RagSourceCreate,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    resolved = await resolve_company_id(current, db)
    if company_id != resolved and not current.is_super_admin:
        raise HTTPException(status_code=403, detail="Sem acesso")
    config = await _get_default_ai_config(db, company_id)
    rag = RagSource(
        company_id=company_id,
        ai_config_id=config.id,
        name=payload.name,
        source_type=payload.source_type,
        webhook_url=payload.webhook_url,
    )
    db.add(rag)
    await db.flush()
    await db.refresh(rag)
    return rag


@router.get("/ai-configs/{company_id}/rag-sources", response_model=list[RagSourceOut])
async def list_rag_sources(
    company_id: UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    resolved = await resolve_company_id(current, db)
    if company_id != resolved and not current.is_super_admin:
        raise HTTPException(status_code=403, detail="Sem acesso")
    result = await db.execute(select(RagSource).where(RagSource.company_id == company_id))
    return result.scalars().all()


@router.patch("/ai-configs/{company_id}/rag-sources/{rag_id}", response_model=RagSourceOut)
async def update_rag_source(
    company_id: UUID,
    rag_id: UUID,
    payload: RagSourceUpdate,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    resolved = await resolve_company_id(current, db)
    if company_id != resolved and not current.is_super_admin:
        raise HTTPException(status_code=403, detail="Sem acesso")
    rag = await db.get(RagSource, rag_id)
    if not rag or rag.company_id != company_id:
        raise HTTPException(status_code=404, detail="RAG não encontrada")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(rag, k, v)
    await db.flush()
    await db.refresh(rag)
    return rag


@router.delete("/ai-configs/{company_id}/rag-sources/{rag_id}", status_code=204)
async def delete_rag_source(
    company_id: UUID,
    rag_id: UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    resolved = await resolve_company_id(current, db)
    if company_id != resolved and not current.is_super_admin:
        raise HTTPException(status_code=403, detail="Sem acesso")
    rag = await db.get(RagSource, rag_id)
    if not rag or rag.company_id != company_id:
        raise HTTPException(status_code=404, detail="RAG não encontrada")
    await db.delete(rag)


@router.post("/ai-configs/{company_id}/tools", response_model=ToolOut)
async def create_tool(
    company_id: UUID,
    payload: ToolCreate,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    resolved = await resolve_company_id(current, db)
    if company_id != resolved and not current.is_super_admin:
        raise HTTPException(status_code=403, detail="Sem acesso")
    config = await _get_default_ai_config(db, company_id)
    tool = Tool(
        company_id=company_id,
        ai_config_id=config.id,
        **payload.model_dump(),
    )
    db.add(tool)
    await db.flush()
    await db.refresh(tool)
    return tool


@router.get("/ai-configs/{company_id}/tools", response_model=list[ToolOut])
async def list_tools(
    company_id: UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    resolved = await resolve_company_id(current, db)
    if company_id != resolved and not current.is_super_admin:
        raise HTTPException(status_code=403, detail="Sem acesso")
    result = await db.execute(select(Tool).where(Tool.company_id == company_id).order_by(Tool.created_at.desc()))
    return result.scalars().all()


@router.patch("/ai-configs/{company_id}/tools/{tool_id}", response_model=ToolOut)
async def update_tool(
    company_id: UUID,
    tool_id: UUID,
    payload: ToolUpdate,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    resolved = await resolve_company_id(current, db)
    if company_id != resolved and not current.is_super_admin:
        raise HTTPException(status_code=403, detail="Sem acesso")
    tool = await db.get(Tool, tool_id)
    if not tool or tool.company_id != company_id:
        raise HTTPException(status_code=404, detail="Ferramenta não encontrada")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(tool, k, v)
    await db.flush()
    await db.refresh(tool)
    return tool


@router.delete("/ai-configs/{company_id}/tools/{tool_id}", status_code=204)
async def delete_tool(
    company_id: UUID,
    tool_id: UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    resolved = await resolve_company_id(current, db)
    if company_id != resolved and not current.is_super_admin:
        raise HTTPException(status_code=403, detail="Sem acesso")
    tool = await db.get(Tool, tool_id)
    if not tool or tool.company_id != company_id:
        raise HTTPException(status_code=404, detail="Ferramenta não encontrada")
    await db.delete(tool)


async def _get_test_conversation(db: AsyncSession, company_id: UUID) -> Conversation | None:
    result = await db.execute(
        select(Conversation).where(
            Conversation.company_id == company_id,
            Conversation.contact_phone == TEST_CHAT_PHONE,
        )
    )
    return result.scalar_one_or_none()


@router.post("/ai-configs/{company_id}/test-chat", response_model=TestChatResponse)
async def test_chat(
    company_id: UUID,
    payload: TestChatRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Playground de conversa: gera a resposta da IA sem enviar nada pelo WhatsApp real."""
    resolved = await resolve_company_id(current, db)
    if company_id != resolved and not current.is_super_admin:
        raise HTTPException(status_code=403, detail="Sem acesso")

    conv = await _get_test_conversation(db, company_id)
    if not conv:
        conv = Conversation(
            company_id=company_id,
            contact_phone=TEST_CHAT_PHONE,
            contact_name="Chat de teste",
            channel="test_console",
            ai_enabled=True,
            status="open",
        )
        db.add(conv)
        await db.flush()

    conv.ai_enabled = True
    conv.last_message_at = datetime.now(timezone.utc)
    # Deixa escolher qual integração simular (ou nenhuma = ferramentas
    # globais) — sem isso, ferramentas cadastradas só pra uma integração
    # específica nunca apareceriam pra IA aqui, mesmo estando corretas.
    conv.integration_id = payload.integration_id

    inbound = NormalizedMessageEvent(
        event_type="message_inbound",
        external_message_id=None,
        external_conversation_id=None,
        channel_to=None,
        contact_phone=TEST_CHAT_PHONE,
        content_type="text",
        text=payload.text,
        timestamp=datetime.now(timezone.utc),
        actor="customer",
        raw_payload={"test_console": True},
    )
    await save_message(db, conv, inbound)

    # Debounce curto no playground: agrega rajadas rápidas sem imitar os 8s
    # do WhatsApp — e libera o lock mais cedo pro turno seguinte (horário →
    # estacionamento etc.).
    settings = get_settings()
    test_debounce = min(settings.ai_reply_debounce_seconds, 1.5)
    schedule_ai_reply(conv.id, company_id, test_debounce)

    return TestChatResponse(conversation_id=conv.id, reply=None)


@router.get("/ai-configs/{company_id}/test-chat/messages", response_model=list[MessageOut])
async def list_test_chat_messages(
    company_id: UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    resolved = await resolve_company_id(current, db)
    if company_id != resolved and not current.is_super_admin:
        raise HTTPException(status_code=403, detail="Sem acesso")

    conv = await _get_test_conversation(db, company_id)
    if not conv:
        return []
    result = await db.execute(
        select(Message).where(Message.conversation_id == conv.id).order_by(Message.created_at.asc())
    )
    return result.scalars().all()


@router.delete("/ai-configs/{company_id}/test-chat", status_code=204)
async def reset_test_chat(
    company_id: UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    resolved = await resolve_company_id(current, db)
    if company_id != resolved and not current.is_super_admin:
        raise HTTPException(status_code=403, detail="Sem acesso")

    conv = await _get_test_conversation(db, company_id)
    if conv:
        await db.delete(conv)
