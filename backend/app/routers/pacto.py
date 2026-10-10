from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import CurrentUser, get_current_user, resolve_company_id
from app.models import PactoUnit, Unit
from app.services import pacto

router = APIRouter(prefix="/pacto", tags=["pacto"])


class PactoUnitOut(BaseModel):
    id: UUID
    pacto_name: str
    unit_id: UUID | None
    movement_text: str | None
    synced_at: datetime | None

    model_config = {"from_attributes": True}


class PactoStatus(BaseModel):
    status: str = "disconnected"
    enabled: bool = True
    last_error: str | None = None
    connected_at: datetime | None = None
    last_sync_at: datetime | None = None
    units: list[PactoUnitOut] = Field(default_factory=list)


class ConnectRequest(BaseModel):
    # Endereço do painel pra onde a Pacto devolve o usuário depois de autorizar.
    redirect_uri: str = Field(pattern=r"^https?://[^\s]+/pacto/callback$")


class ConnectResponse(BaseModel):
    authorization_url: str


class CallbackRequest(BaseModel):
    code: str
    state: str


class PactoUpdate(BaseModel):
    enabled: bool


class PactoUnitUpdate(BaseModel):
    unit_id: UUID | None


async def _status(db: AsyncSession, company_id: UUID) -> PactoStatus:
    connection = await pacto.get_connection(db, company_id)
    if not connection:
        return PactoStatus()
    units = (
        await db.execute(
            select(PactoUnit).where(PactoUnit.company_id == company_id).order_by(PactoUnit.pacto_name)
        )
    ).scalars().all()
    return PactoStatus(
        status=connection.status,
        enabled=connection.enabled,
        last_error=connection.last_error,
        connected_at=connection.connected_at,
        last_sync_at=connection.last_sync_at,
        units=[PactoUnitOut.model_validate(u) for u in units],
    )


@router.get("", response_model=PactoStatus)
async def pacto_status(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _status(db, await resolve_company_id(current, db))


@router.post("/connect", response_model=ConnectResponse)
async def pacto_connect(
    payload: ConnectRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Começa a conexão: devolve o endereço da tela de autorização da Pacto."""
    company_id = await resolve_company_id(current, db)
    try:
        url = await pacto.start_authorization(db, company_id, payload.redirect_uri)
    except pacto.PactoError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="Não foi possível falar com a Pacto agora.") from exc
    return ConnectResponse(authorization_url=url)


@router.post("/callback", response_model=PactoStatus)
async def pacto_callback(
    payload: CallbackRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Conclui a conexão com o código que a Pacto devolveu e já faz a
    primeira sincronização."""
    company_id = await resolve_company_id(current, db)
    try:
        connection = await pacto.finish_authorization(db, company_id, payload.code, payload.state)
        await pacto.sync_movement(db, connection)
    except pacto.PactoError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await _status(db, company_id)


@router.post("/sync", response_model=PactoStatus)
async def pacto_sync(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Atualiza agora (unidades liberadas + movimento), sem esperar a rotina diária."""
    company_id = await resolve_company_id(current, db)
    connection = await pacto.get_connection(db, company_id)
    if not connection or connection.status == "disconnected":
        raise HTTPException(status_code=400, detail="Pacto não está conectada.")
    try:
        await pacto.discover_units(db, connection)
        await pacto.sync_movement(db, connection)
    except pacto.PactoError as exc:
        # O status "error" marcado na conexão precisa ser salvo mesmo com a falha.
        await db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return await _status(db, company_id)


@router.patch("", response_model=PactoStatus)
async def pacto_update(
    payload: PactoUpdate,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    connection = await pacto.get_connection(db, company_id)
    if not connection:
        raise HTTPException(status_code=404, detail="Pacto não está conectada.")
    connection.enabled = payload.enabled
    await db.flush()
    return await _status(db, company_id)


@router.patch("/units/{pacto_unit_id}", response_model=PactoStatus)
async def pacto_update_unit(
    pacto_unit_id: UUID,
    payload: PactoUnitUpdate,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Corrige a qual unidade do catálogo uma unidade da Pacto corresponde."""
    company_id = await resolve_company_id(current, db)
    pacto_unit = await db.get(PactoUnit, pacto_unit_id)
    if not pacto_unit or pacto_unit.company_id != company_id:
        raise HTTPException(status_code=404, detail="Unidade não encontrada.")
    if payload.unit_id is not None:
        unit = await db.get(Unit, payload.unit_id)
        if not unit or unit.company_id != company_id:
            raise HTTPException(status_code=404, detail="Unidade do catálogo não encontrada.")
    pacto_unit.unit_id = payload.unit_id
    await db.flush()
    return await _status(db, company_id)


@router.delete("", response_model=PactoStatus)
async def pacto_disconnect(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    connection = await pacto.get_connection(db, company_id)
    if connection:
        await pacto.disconnect(db, connection)
    return await _status(db, company_id)
