import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import CurrentUser, get_current_user, resolve_company_id
from app.models import AiConfig, Integration, RagSource, SystemEvent, WebhookLog

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/health")
async def health(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    db_ok = True
    try:
        await db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False

    company_id = None
    try:
        company_id = await resolve_company_id(current, db)
    except Exception:  # noqa: BLE001
        pass

    integrations = 0
    rags = 0
    has_llm_key = False
    if company_id:
        integrations = await db.scalar(
            select(func.count())
            .select_from(Integration)
            .where(Integration.company_id == company_id, Integration.is_active.is_(True))
        )
        rags = await db.scalar(
            select(func.count())
            .select_from(RagSource)
            .where(RagSource.company_id == company_id, RagSource.is_active.is_(True))
        )
        cfg = await db.scalar(
            select(AiConfig).where(AiConfig.company_id == company_id, AiConfig.integration_id.is_(None))
        )
        has_llm_key = bool(cfg and cfg.llm_api_key_encrypted)

    return {
        "status": "ok" if db_ok else "degraded",
        "database": db_ok,
        "integrations_active": integrations or 0,
        "rag_sources_active": rags or 0,
        "llm_configured": has_llm_key,
    }


@router.get("/webhooks/logs")
async def webhook_logs(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = 50,
):
    company_id = await resolve_company_id(current, db)
    result = await db.execute(
        select(WebhookLog)
        .where(WebhookLog.company_id == company_id)
        .order_by(WebhookLog.created_at.desc())
        .limit(limit)
    )
    logs = result.scalars().all()
    return [
        {
            "id": str(log.id),
            "direction": log.direction,
            "status": log.status,
            "http_status": log.http_status,
            "error_message": log.error_message,
            "created_at": log.created_at.isoformat() if log.created_at else None,
            "payload": log.payload,
        }
        for log in logs
    ]


def _event_out(event: SystemEvent) -> dict:
    return {
        "id": str(event.id),
        "kind": event.kind,
        "severity": event.severity,
        "key": event.key,
        "title": event.title,
        "body": event.body,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "resolved_at": event.resolved_at.isoformat() if event.resolved_at else None,
        "dismissed_at": event.dismissed_at.isoformat() if event.dismissed_at else None,
    }


@router.get("/events")
async def list_events(
    kind: str | None = None,
    active: bool = False,
    limit: int = 50,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Alertas de saúde e relatórios diários. `active=true` traz só os
    alertas ainda em aberto e não dispensados."""
    company_id = await resolve_company_id(current, db)
    query = select(SystemEvent).where(SystemEvent.company_id == company_id)
    if kind in {"alert", "report"}:
        query = query.where(SystemEvent.kind == kind)
    if active:
        query = query.where(SystemEvent.resolved_at.is_(None), SystemEvent.dismissed_at.is_(None))
    result = await db.execute(query.order_by(SystemEvent.created_at.desc()).limit(min(max(limit, 1), 200)))
    return [_event_out(e) for e in result.scalars().all()]


@router.get("/events/active-count")
async def active_alert_count(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    count = await db.scalar(
        select(func.count())
        .select_from(SystemEvent)
        .where(
            SystemEvent.company_id == company_id,
            SystemEvent.kind == "alert",
            SystemEvent.resolved_at.is_(None),
            SystemEvent.dismissed_at.is_(None),
        )
    )
    return {"count": count or 0}


@router.post("/events/{event_id}/dismiss")
async def dismiss_event(
    event_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    event = await db.scalar(
        select(SystemEvent).where(SystemEvent.id == event_id, SystemEvent.company_id == company_id)
    )
    if not event:
        raise HTTPException(status_code=404, detail="Alerta não encontrado")
    if event.dismissed_at is None:
        event.dismissed_at = datetime.now(timezone.utc)
        await db.commit()
    return _event_out(event)


@router.get("/endpoints")
async def list_endpoints(current: CurrentUser = Depends(get_current_user)):
    """Catálogo vivo — o frontend também pode ler /openapi.json diretamente."""
    return {
        "openapi_url": "/openapi.json",
        "docs_url": "/docs",
        "hint": "Use o schema OpenAPI para montar o catálogo no painel.",
    }
