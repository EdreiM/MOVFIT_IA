from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_api_key_company, get_sales_writer_company
from app.models import Lead, SalesContract
from app.routers.metrics import (
    _compute_featured_tools_stats,
    _compute_leads_funnel,
    _compute_overview,
    _compute_tools_stats,
    _default_operations_period,
)
from app.schemas import (
    AiOperationsReport,
    LeadOut,
    MetricsNarrativeReport,
    MetricsOverview,
    ShowcaseExample,
    StageCount,
    ToolStats,
)
from app.services import sales_contracts
from app.services.metrics_narrative import compute_metrics_narrative
from app.services.metrics_operations import compute_ai_operations_report
from app.services.metrics_showcase import compute_metrics_showcase

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
    days: int | None = Query(None, ge=1, le=365),
    company_id: UUID = Depends(get_api_key_company),
    db: AsyncSession = Depends(get_db),
):
    return await _compute_tools_stats(db, company_id, days=days)


@router.get("/metrics/featured-tools", response_model=list[ToolStats])
async def featured_tools_stats_external(
    company_id: UUID = Depends(get_api_key_company),
    db: AsyncSession = Depends(get_db),
):
    return await _compute_featured_tools_stats(db, company_id)


@router.get("/metrics/narrative-report", response_model=MetricsNarrativeReport)
async def metrics_narrative_report_external(
    company_id: UUID = Depends(get_api_key_company),
    db: AsyncSession = Depends(get_db),
):
    return await compute_metrics_narrative(db, company_id)


@router.get("/metrics/showcase-examples", response_model=list[ShowcaseExample])
async def metrics_showcase_examples_external(
    company_id: UUID = Depends(get_api_key_company),
    db: AsyncSession = Depends(get_db),
):
    return await compute_metrics_showcase(db, company_id)


@router.get("/metrics/operations-report", response_model=AiOperationsReport)
async def metrics_operations_report_external(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    company_id: UUID = Depends(get_api_key_company),
    db: AsyncSession = Depends(get_db),
):
    default_from, default_to = _default_operations_period()
    return await compute_ai_operations_report(
        db,
        company_id,
        date_from=date_from or default_from,
        date_to=date_to or default_to,
    )


# --- Vendas (ranking) -----------------------------------------------------


class SalesContractsIn(BaseModel):
    """Um produto (matrícula ou rematrícula) de uma unidade, num período — o
    corpo cru de `faturamento-sintetico/resumo-pessoa` (campo `content`),
    como o fluxo do n8n recebe da Pacto."""

    unidade: str = Field(min_length=1, max_length=100)
    produto: str = Field(min_length=1, max_length=64)
    data_inicio: date
    data_fim: date
    # `totalElements` da Pacto. Com ele o painel sabe se a lista veio inteira
    # (só então remove o que sumiu do período).
    total: int | None = Field(default=None, ge=0)
    contratos: list[dict] = Field(default_factory=list, max_length=sales_contracts.MAX_CONTRACTS_PER_REQUEST)


@router.post("/sales/contracts")
async def ingest_sales_contracts(
    body: SalesContractsIn,
    company_id: UUID = Depends(get_sales_writer_company),
    db: AsyncSession = Depends(get_db),
):
    """Recebe do n8n os contratos de venda nova de uma unidade (alimenta o
    ranking de vendas). Idempotente: reenviar o mesmo período só atualiza."""
    if body.data_inicio > body.data_fim:
        raise HTTPException(status_code=400, detail="data_inicio é depois de data_fim.")
    if (body.data_fim - body.data_inicio).days > 366:
        raise HTTPException(status_code=400, detail="Período longo demais (máximo de 1 ano).")
    try:
        result = await sales_contracts.ingest(
            db,
            company_id,
            unidade=body.unidade,
            produto=body.produto,
            data_inicio=body.data_inicio,
            data_fim=body.data_fim,
            total=body.total,
            records=body.contratos,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await db.commit()
    return {"sucesso": True, "mensagem": "Contratos gravados.", "dados": result}


@router.get("/sales/contracts")
async def list_sales_contracts(
    date_from: date | None = None,
    date_to: date | None = None,
    unidade: str | None = None,
    responsavel: str | None = None,
    limit: int = Query(1000, ge=1, le=5000),
    company_id: UUID = Depends(get_api_key_company),
    db: AsyncSession = Depends(get_db),
):
    """Lista os contratos de venda nova guardados (mesma base do ranking) —
    pra usar em fluxos do n8n como mais uma fonte, ao lado do MCP."""
    stmt = select(SalesContract).where(SalesContract.company_id == company_id)
    if date_from:
        stmt = stmt.where(SalesContract.lancamento >= date_from)
    if date_to:
        stmt = stmt.where(SalesContract.lancamento <= date_to)
    if unidade:
        stmt = stmt.where(SalesContract.unidade == unidade)
    if responsavel:
        stmt = stmt.where(SalesContract.responsavel.ilike(f"%{responsavel}%"))
    stmt = stmt.order_by(SalesContract.lancamento.desc(), SalesContract.contrato.desc()).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "sucesso": True,
        "mensagem": "",
        "dados": {
            "total": len(rows),
            "contratos": [
                {
                    "unidade": r.unidade,
                    "empresa": r.empresa_nome,
                    "contrato": r.contrato,
                    "matricula": r.matricula,
                    "cliente": r.cliente_nome,
                    "responsavel": r.responsavel,
                    "consultor": r.consultor,
                    "plano": r.plano,
                    "duracao": r.duracao,
                    "produto": r.produto,
                    "anual": sales_contracts.is_annual(r.duracao, r.plano),
                    "lancamento": r.lancamento.isoformat(),
                }
                for r in rows
            ],
        },
    }
