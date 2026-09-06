from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import CurrentUser, get_current_user, require_roles, resolve_company_id
from app.models import Lead
from app.schemas import LeadOut, LeadUpdate

router = APIRouter(prefix="/leads", tags=["leads"])


@router.get("", response_model=list[LeadOut])
async def list_leads(
    q: str | None = None,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    stmt = select(Lead).where(Lead.company_id == company_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Lead.name.ilike(like), Lead.phone.ilike(like), Lead.cpf.ilike(like), Lead.email.ilike(like)))
    stmt = stmt.order_by(Lead.updated_at.desc())
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/{lead_id}", response_model=LeadOut)
async def get_lead(
    lead_id: UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    lead = await db.get(Lead, lead_id)
    if not lead or lead.company_id != company_id:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")
    return lead


@router.patch("/{lead_id}", response_model=LeadOut)
async def update_lead(
    lead_id: UUID,
    payload: LeadUpdate,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    lead = await db.get(Lead, lead_id)
    if not lead or lead.company_id != company_id:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(lead, k, v)
    await db.flush()
    await db.refresh(lead)
    return lead


@router.delete("/{lead_id}", status_code=204)
async def delete_lead(
    lead_id: UUID,
    current: CurrentUser = Depends(require_roles("super_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Restrito a super_admin, mesmo critério das conversas de teste/dev."""
    company_id = await resolve_company_id(current, db)
    lead = await db.get(Lead, lead_id)
    if not lead or lead.company_id != company_id:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")
    await db.delete(lead)
