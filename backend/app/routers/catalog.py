import os
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import get_db
from app.deps import CurrentUser, get_current_user, resolve_company_id
from app.models import Plan, Unit
from app.schemas import PlanCreate, PlanOut, PlanUpdate, UnitCreate, UnitOut, UnitUpdate

router = APIRouter(prefix="/units", tags=["catalog"])

UPLOADS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "uploads", "plans")
ALLOWED_IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024


async def _get_unit_or_404(db: AsyncSession, unit_id: UUID, company_id: UUID) -> Unit:
    unit = await db.get(Unit, unit_id)
    if not unit or unit.company_id != company_id:
        raise HTTPException(status_code=404, detail="Unidade não encontrada")
    return unit


@router.get("", response_model=list[UnitOut])
async def list_units(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    result = await db.execute(
        select(Unit)
        .options(selectinload(Unit.plans))
        .where(Unit.company_id == company_id)
        .order_by(Unit.city, Unit.name)
    )
    return result.scalars().unique().all()


@router.post("", response_model=UnitOut)
async def create_unit(
    payload: UnitCreate,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    unit = Unit(company_id=company_id, **payload.model_dump())
    db.add(unit)
    await db.flush()
    await db.refresh(unit, attribute_names=["plans"])
    return unit


@router.patch("/{unit_id}", response_model=UnitOut)
async def update_unit(
    unit_id: UUID,
    payload: UnitUpdate,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    unit = await _get_unit_or_404(db, unit_id, company_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(unit, k, v)
    await db.flush()
    await db.refresh(unit, attribute_names=["plans"])
    return unit


@router.delete("/{unit_id}", status_code=204)
async def delete_unit(
    unit_id: UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    unit = await _get_unit_or_404(db, unit_id, company_id)
    await db.delete(unit)


@router.post("/{unit_id}/plans", response_model=PlanOut)
async def create_plan(
    unit_id: UUID,
    payload: PlanCreate,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    unit = await _get_unit_or_404(db, unit_id, company_id)
    plan = Plan(company_id=company_id, unit_id=unit.id, **payload.model_dump())
    db.add(plan)
    await db.flush()
    await db.refresh(plan)
    return plan


plans_router = APIRouter(prefix="/plans", tags=["catalog"])


async def _get_plan_or_404(db: AsyncSession, plan_id: UUID, company_id: UUID) -> Plan:
    plan = await db.get(Plan, plan_id)
    if not plan or plan.company_id != company_id:
        raise HTTPException(status_code=404, detail="Plano não encontrado")
    return plan


@plans_router.patch("/{plan_id}", response_model=PlanOut)
async def update_plan(
    plan_id: UUID,
    payload: PlanUpdate,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    plan = await _get_plan_or_404(db, plan_id, company_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(plan, k, v)
    await db.flush()
    await db.refresh(plan)
    return plan


@plans_router.post("/{plan_id}/image", response_model=PlanOut)
async def upload_plan_image(
    plan_id: UUID,
    file: UploadFile = File(...),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    plan = await _get_plan_or_404(db, plan_id, company_id)

    ext = ALLOWED_IMAGE_TYPES.get(file.content_type)
    if not ext:
        raise HTTPException(status_code=400, detail="Envie uma imagem JPEG, PNG ou WEBP")

    content = await file.read()
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="Imagem muito grande (máximo 5MB)")

    os.makedirs(UPLOADS_DIR, exist_ok=True)
    filename = f"{plan.id}{ext}"
    with open(os.path.join(UPLOADS_DIR, filename), "wb") as f:
        f.write(content)

    settings = get_settings()
    plan.image_url = f"{settings.public_base_url.rstrip('/')}/uploads/plans/{filename}"
    await db.flush()
    await db.refresh(plan)
    return plan


@plans_router.delete("/{plan_id}", status_code=204)
async def delete_plan(
    plan_id: UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    plan = await _get_plan_or_404(db, plan_id, company_id)
    await db.delete(plan)
