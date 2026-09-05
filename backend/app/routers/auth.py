import time

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

# Rate limit simples em memória — ok pra 1 instância do backend (é o caso
# aqui); se um dia rodar múltiplas réplicas, precisaria mover isso pra um
# storage compartilhado (Redis, etc.) pra valer entre elas.
MAX_LOGIN_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 5 * 60
_failed_attempts: dict[str, list[float]] = {}


def _register_failed_attempt(key: str) -> None:
    now = time.time()
    attempts = [t for t in _failed_attempts.get(key, []) if now - t < LOGIN_WINDOW_SECONDS]
    attempts.append(now)
    _failed_attempts[key] = attempts


def _is_locked_out(key: str) -> bool:
    now = time.time()
    attempts = [t for t in _failed_attempts.get(key, []) if now - t < LOGIN_WINDOW_SECONDS]
    _failed_attempts[key] = attempts
    return len(attempts) >= MAX_LOGIN_ATTEMPTS


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    rate_key = payload.email.strip().lower()
    if _is_locked_out(rate_key):
        raise HTTPException(
            status_code=429,
            detail="Muitas tentativas de login. Tente novamente em alguns minutos.",
        )

    result = await db.execute(
        select(User).options(selectinload(User.companies)).where(User.email == payload.email)
    )
    user = result.scalar_one_or_none()
    if not user or not verify_password(payload.password, user.hashed_password):
        _register_failed_attempt(rate_key)
        raise HTTPException(status_code=401, detail="E-mail ou senha inválidos")
    _failed_attempts.pop(rate_key, None)
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
