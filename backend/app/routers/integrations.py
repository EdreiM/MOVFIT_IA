import json
import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import resolve_adapter
from app.database import get_db
from app.deps import CurrentUser, get_current_user, resolve_company_id
from app.models import Integration, Number, WebhookLog
from app.schemas import IntegrationCreate, IntegrationOut, IntegrationUpdate
from app.security import encrypt_secret
from app.services.message_flow import process_normalized_event

router = APIRouter(tags=["integrations"])


@router.get("/integrations", response_model=list[IntegrationOut])
async def list_integrations(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    result = await db.execute(
        select(Integration).where(Integration.company_id == company_id).order_by(Integration.created_at.desc())
    )
    return result.scalars().all()


@router.post("/integrations/webhook", response_model=IntegrationOut)
async def create_webhook_integration(
    payload: IntegrationCreate,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    config = payload.config or {}
    if payload.adapter_key == "evolution_api_v1":
        raw_token = config.get("instance_token")
        if raw_token:
            config = {**config, "instance_token_encrypted": encrypt_secret(str(raw_token))}
            config.pop("instance_token", None)
    integ = Integration(
        company_id=company_id,
        name=payload.name,
        integration_type=payload.integration_type or "webhook",
        adapter_key=payload.adapter_key,
        inbound_secret=secrets.token_urlsafe(24),
        outbound_url=payload.outbound_url,
        field_mapping=payload.field_mapping or {},
        config=config,
    )
    db.add(integ)
    await db.flush()
    await db.refresh(integ)
    return integ


@router.post("/integrations/chatwoot", response_model=IntegrationOut)
async def create_chatwoot_integration(
    payload: IntegrationCreate,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    integ = Integration(
        company_id=company_id,
        name=payload.name or "Chatwoot",
        integration_type="chatwoot",
        adapter_key=payload.adapter_key or "chatwoot_v1",
        inbound_secret=secrets.token_urlsafe(24),
        outbound_url=payload.outbound_url,
        field_mapping=payload.field_mapping or {},
        config=payload.config or {},
    )
    db.add(integ)
    await db.flush()
    await db.refresh(integ)
    return integ


@router.patch("/integrations/{integration_id}", response_model=IntegrationOut)
async def update_integration(
    integration_id: UUID,
    payload: IntegrationUpdate,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    integ = await db.get(Integration, integration_id)
    if not integ or integ.company_id != company_id:
        raise HTTPException(status_code=404, detail="Integração não encontrada")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(integ, k, v)
    await db.flush()
    await db.refresh(integ)
    return integ


@router.delete("/integrations/{integration_id}", status_code=204)
async def delete_integration(
    integration_id: UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    integ = await db.get(Integration, integration_id)
    if not integ or integ.company_id != company_id:
        raise HTTPException(status_code=404, detail="Integração não encontrada")
    await db.delete(integ)


@router.post("/integrations/{integration_id}/regenerate-secret", response_model=IntegrationOut)
async def regenerate_inbound_secret(
    integration_id: UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    integ = await db.get(Integration, integration_id)
    if not integ or integ.company_id != company_id:
        raise HTTPException(status_code=404, detail="Integração não encontrada")
    integ.inbound_secret = secrets.token_urlsafe(24)
    await db.flush()
    await db.refresh(integ)
    return integ


@router.post("/webhooks/inbound/{integration_id}/{secret}")
async def inbound_webhook(
    integration_id: UUID,
    secret: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    integ = await db.get(Integration, integration_id)
    # Mesma resposta (404) pra integração inexistente e pra segredo errado —
    # não dá pra quem está tentando adivinhar saber qual dos dois errou.
    if not integ or not integ.is_active or not integ.inbound_secret:
        raise HTTPException(status_code=404, detail="Integração não encontrada")
    if not secrets.compare_digest(secret, integ.inbound_secret):
        raise HTTPException(status_code=404, detail="Integração não encontrada")

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        raw_body = (await request.body()).decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError:
            payload = {"raw": raw_body}

    if isinstance(payload, list) and payload:
        payload = payload[0]

    log = WebhookLog(
        company_id=integ.company_id,
        integration_id=integ.id,
        direction="inbound",
        status="received",
        payload=payload if isinstance(payload, dict) else {"raw": str(payload)},
    )
    db.add(log)
    await db.flush()

    try:
        event = resolve_adapter(
            integ.adapter_key,
            payload if isinstance(payload, dict) else {},
            integ.field_mapping,
            integ.config,
        )
        number = None
        if event.channel_to:
            result = await db.execute(
                select(Number).where(
                    Number.company_id == integ.company_id,
                    Number.phone == event.channel_to,
                )
            )
            number = result.scalar_one_or_none()

        result = await process_normalized_event(db, integ.company_id, event, number, integration_id=integ.id)
        log.status = "ok"
        log.http_status = 200
        return result
    except Exception as exc:  # noqa: BLE001
        # Retorna dict (não HTTPException) para o get_db fazer commit do log de erro
        log.status = "error"
        log.http_status = 400
        log.error_message = str(exc)
        await db.flush()
        return {"status": "error", "detail": f"Falha no adaptador: {exc}"}
