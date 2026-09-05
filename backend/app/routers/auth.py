from fastapi import APIRouter, Depends, HTTPException
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.deps import CurrentUser, get_current_user
from app.models import Company, User, UserCompany
from app.schemas import CompanyOut, LoginRequest, RefreshRequest, TokenResponse, UserOut
from app.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(User).options(selectinload(User.companies)).where(User.email == payload.email)
    )
    user = result.scalar_one_or_none()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="E-mail ou senha inválidos")
    if user.status != "active":
        raise HTTPException(status_code=403, detail="Usuário inativo")
    if user.role == "atendente":
        raise HTTPException(status_code=403, detail="Atendentes não acessam o painel no MVP")

    company_id = user.companies[0].company_id if user.companies else None
    return TokenResponse(
        access_token=create_access_token(user.id, user.role, company_id),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest, db: AsyncSession = Depends(get_db)):
    try:
        data = decode_token(payload.refresh_token)
        if data.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Refresh token inválido")
        from uuid import UUID

        user_id = UUID(data["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Refresh token inválido")

    result = await db.execute(
        select(User).options(selectinload(User.companies)).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user or user.status != "active":
        raise HTTPException(status_code=401, detail="Usuário inválido")

    company_id = user.companies[0].company_id if user.companies else None
    return TokenResponse(
        access_token=create_access_token(user.id, user.role, company_id),
        refresh_token=create_refresh_token(user.id),
    )


@router.get("/me", response_model=UserOut)
async def me(current: CurrentUser = Depends(get_current_user)):
    return current.user


@router.get("/me/companies", response_model=list[CompanyOut])
async def my_companies(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current.is_super_admin:
        result = await db.execute(select(Company).order_by(Company.name))
        return result.scalars().all()
    company_ids = [uc.company_id for uc in current.user.companies]
    if not company_ids:
        return []
    result = await db.execute(select(Company).where(Company.id.in_(company_ids)).order_by(Company.name))
    return result.scalars().all()
