from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import CurrentUser, get_current_user, resolve_company_id
from app.models import Number
from app.schemas import NumberCreate, NumberOut, NumberUpdate

router = APIRouter(prefix="/numbers", tags=["numbers"])


@router.get("", response_model=list[NumberOut])
async def list_numbers(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    result = await db.execute(
        select(Number).where(Number.company_id == company_id).order_by(Number.created_at.desc())
    )
    return result.scalars().all()


@router.post("", response_model=NumberOut)
async def create_number(
    payload: NumberCreate,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    number = Number(company_id=company_id, **payload.model_dump())
    db.add(number)
    await db.flush()
    await db.refresh(number)
    return number


@router.patch("/{number_id}", response_model=NumberOut)
async def update_number(
    number_id: UUID,
    payload: NumberUpdate,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    number = await db.get(Number, number_id)
    if not number or number.company_id != company_id:
        raise HTTPException(status_code=404, detail="Número não encontrado")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(number, k, v)
    await db.flush()
    await db.refresh(number)
    return number


@router.delete("/{number_id}")
async def delete_number(
    number_id: UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    number = await db.get(Number, number_id)
    if not number or number.company_id != company_id:
        raise HTTPException(status_code=404, detail="Número não encontrado")
    await db.delete(number)
    return {"status": "ok"}
