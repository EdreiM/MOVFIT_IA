from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_api_key_company
from app.models import Lead
from app.routers.metrics import (
    _compute_featured_tools_stats,
    _compute_leads_funnel,
    _compute_overview,
    _compute_tools_stats,
)
from app.schemas import LeadOut, MetricsOverview, StageCount, ToolStats

# Sem "/api" aqui de propósito — em produção isso já é adicionado (e
# descascado) pelo proxy reverso na frente do backend, igual todo resto
# da API (ver /leads, /metrics, etc. nos outros routers). Só com prefixo
# "/v1", o caminho público final (depois do proxy) fica /api/v1/... —
# que é o que sistemas externos devem chamar, ver ApiKeysPage "Como usar".
router = APIRouter(prefix="/v1", tags=["public-api"])


@router.get("/leads", response_model=list[LeadOut])
async def list_leads_external(
    stage: str | None = None,
    updated_since: datetime | None = None,
    limit: int = Query(200, le=1000),
    company_id: UUID = Depends(get_api_key_company),
    db: AsyncSession = Depends(get_db),
):
    """Exportação de leads pra sistemas externos (n8n, BI, etc) — mesmos
    dados da tela Clientes do painel. `updated_since` (ISO 8601) permite
    sincronização incremental, pegando só quem mudou desde a última
    consulta."""
    stmt = select(Lead).where(Lead.company_id == company_id)
    if stage:
        stmt = stmt.where(Lead.stage == stage)
    if updated_since:
        stmt = stmt.where(Lead.updated_at >= updated_since)
    stmt = stmt.order_by(Lead.updated_at.desc()).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/metrics/overview", response_model=MetricsOverview)
async def metrics_overview_external(
    company_id: UUID = Depends(get_api_key_company),
    db: AsyncSession = Depends(get_db),
):
    return await _compute_overview(db, company_id)


@router.get("/metrics/leads-funnel", response_model=list[StageCount])
async def leads_funnel_external(
    company_id: UUID = Depends(get_api_key_company),
    db: AsyncSession = Depends(get_db),
):
    return await _compute_leads_funnel(db, company_id)


@router.get("/metrics/tools", response_model=list[ToolStats])
async def tools_stats_external(
    company_id: UUID = Depends(get_api_key_company),
    db: AsyncSession = Depends(get_db),
):
    return await _compute_tools_stats(db, company_id)


@router.get("/metrics/featured-tools", response_model=list[ToolStats])
async def featured_tools_stats_external(
    company_id: UUID = Depends(get_api_key_company),
    db: AsyncSession = Depends(get_db),
):
    return await _compute_featured_tools_stats(db, company_id)
