from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import CurrentUser, get_current_user, require_roles, resolve_company_id
from app.models import ApiKey
from app.schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyOut
from app.security import generate_api_key

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


@router.get("", response_model=list[ApiKeyOut])
async def list_api_keys(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    result = await db.execute(
        select(ApiKey).where(ApiKey.company_id == company_id).order_by(ApiKey.created_at.desc())
    )
    return result.scalars().all()


@router.post("", response_model=ApiKeyCreated)
async def create_api_key(
    payload: ApiKeyCreate,
    current: CurrentUser = Depends(require_roles("super_admin", "admin")),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    full_key, key_prefix, key_hash = generate_api_key()
    api_key = ApiKey(company_id=company_id, name=payload.name, key_prefix=key_prefix, key_hash=key_hash)
    db.add(api_key)
    await db.flush()
    await db.refresh(api_key)
    return ApiKeyCreated(
        id=api_key.id,
        name=api_key.name,
        key_prefix=api_key.key_prefix,
        is_active=api_key.is_active,
        last_used_at=api_key.last_used_at,
        created_at=api_key.created_at,
        key=full_key,
    )


@router.delete("/{api_key_id}", status_code=204)
async def revoke_api_key(
    api_key_id: UUID,
    current: CurrentUser = Depends(require_roles("super_admin", "admin")),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    api_key = await db.get(ApiKey, api_key_id)
    if not api_key or api_key.company_id != company_id:
        raise HTTPException(status_code=404, detail="Chave não encontrada")
    await db.delete(api_key)
