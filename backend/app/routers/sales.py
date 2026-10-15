from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import CurrentUser, get_current_user, resolve_company_id
from app.services import pacto, sales_ranking

router = APIRouter(prefix="/sales", tags=["sales"])

_TZ = ZoneInfo("America/Sao_Paulo")


@router.get("/ranking")
async def ranking(
    unit: str = Query("all", description='"all" ou "<chave_empresa>:<empresa_id>"'),
    date_from: date | None = None,
    date_to: date | None = None,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Ranking de novas matrículas anuais por responsável, no período."""
    company_id = await resolve_company_id(current, db)
    today = datetime.now(_TZ).date()
    date_to = date_to or today
    date_from = date_from or date_to.replace(day=1)
    if date_from > date_to:
        raise HTTPException(status_code=400, detail="A data inicial é depois da final.")
    if (date_to - date_from).days > sales_ranking.MAX_RANGE_DAYS:
        raise HTTPException(status_code=400, detail="Período longo demais (máximo de 1 ano).")
    data = await sales_ranking.build_ranking(db, company_id, unit, date_from, date_to)
    connection = await pacto.get_connection(db, company_id)
    data["conexao"] = connection.status if connection else "disconnected"
    data["conexao_erro"] = connection.last_error if connection else None
    if data["fonte"] == "n8n":
        # Todos os números vêm do n8n: o estado da conexão do MCP não importa.
        data["conexao"] = "connected"
        data["conexao_erro"] = None
    data["atualizando"] = sales_ranking.is_syncing(company_id)
    return data


@router.get("/seller-contracts")
async def seller_contracts(
    name: str = Query(..., min_length=1, description="Nome do responsável, como aparece no ranking"),
    unit: str = Query("all"),
    date_from: date | None = None,
    date_to: date | None = None,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Contratos de um vendedor no período (a lista ao clicar no nome)."""
    company_id = await resolve_company_id(current, db)
    today = datetime.now(_TZ).date()
    date_to = date_to or today
    date_from = date_from or date_to.replace(day=1)
    if date_from > date_to:
        raise HTTPException(status_code=400, detail="A data inicial é depois da final.")
    if (date_to - date_from).days > sales_ranking.MAX_RANGE_DAYS:
        raise HTTPException(status_code=400, detail="Período longo demais (máximo de 1 ano).")
    return await sales_ranking.seller_contracts(db, company_id, unit, date_from, date_to, name)


@router.post("/sync")
async def sync_now(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Atualiza agora, em segundo plano (não espera os 20 minutos da rotina)."""
    company_id = await resolve_company_id(current, db)
    connection = await pacto.get_connection(db, company_id)
    if not connection or connection.status != "connected":
        raise HTTPException(status_code=400, detail="A Pacto não está conectada neste painel.")
    started = await sales_ranking.trigger_sync(company_id)
    return {"iniciado": started, "mensagem": "Atualizando…" if started else "Já está atualizando."}
