from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import CurrentUser, get_current_user, require_roles, resolve_company_id
from app.models import Company, User, UserCompany
from app.schemas import CompanyCreate, CompanyOut, CompanyUpdate, UserCreate, UserOut, UserUpdate
from app.security import hash_password

router = APIRouter(tags=["companies-users"])


@router.get("/companies", response_model=list[CompanyOut])
async def list_companies(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current.is_super_admin:
        result = await db.execute(select(Company).order_by(Company.name))
        return result.scalars().all()
    ids = [uc.company_id for uc in current.user.companies]
    result = await db.execute(select(Company).where(Company.id.in_(ids)))
    return result.scalars().all()


@router.post("/companies", response_model=CompanyOut)
async def create_company(
    payload: CompanyCreate,
    current: CurrentUser = Depends(require_roles("super_admin")),
    db: AsyncSession = Depends(get_db),
):
    exists = await db.execute(select(Company).where(Company.slug == payload.slug))
    if exists.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Slug já existe")
    company = Company(name=payload.name, slug=payload.slug, plan=payload.plan)
    db.add(company)
    await db.flush()
    from app.models import AiConfig

    db.add(AiConfig(company_id=company.id))
    await db.flush()
    await db.refresh(company)
    return company


@router.patch("/companies/{company_id}", response_model=CompanyOut)
async def update_company(
    company_id: UUID,
    payload: CompanyUpdate,
    current: CurrentUser = Depends(require_roles("super_admin", "admin")),
    db: AsyncSession = Depends(get_db),
):
    company = await db.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(company, k, v)
    await db.flush()
    await db.refresh(company)
    return company


@router.get("/users", response_model=list[UserOut])
async def list_users(
    current: CurrentUser = Depends(require_roles("super_admin", "admin")),
    db: AsyncSession = Depends(get_db),
):
    company_id = await resolve_company_id(current, db)
    if current.is_super_admin and not current.company_id:
        result = await db.execute(select(User).order_by(User.email))
        return result.scalars().all()
    result = await db.execute(
        select(User)
        .join(UserCompany, UserCompany.user_id == User.id)
        .where(UserCompany.company_id == company_id)
        .order_by(User.email)
    )
    return result.scalars().unique().all()


@router.post("/users", response_model=UserOut)
async def create_user(
    payload: UserCreate,
    current: CurrentUser = Depends(require_roles("super_admin", "admin")),
    db: AsyncSession = Depends(get_db),
):
    exists = await db.execute(select(User).where(User.email == payload.email))
    if exists.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="E-mail já cadastrado")
    if payload.role == "super_admin" and not current.is_super_admin:
        raise HTTPException(status_code=403, detail="Só super_admin cria super_admin")

    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
    )
    db.add(user)
    await db.flush()

    company_ids = payload.company_ids
    if not company_ids:
        company_ids = [await resolve_company_id(current, db)]
    for cid in company_ids:
        db.add(UserCompany(user_id=user.id, company_id=cid))
    await db.flush()
    await db.refresh(user)
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: UUID,
    payload: UserUpdate,
    current: CurrentUser = Depends(require_roles("super_admin", "admin")),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    data = payload.model_dump(exclude_unset=True)
    if "password" in data and data["password"]:
        user.hashed_password = hash_password(data.pop("password"))
    else:
        data.pop("password", None)
    for k, v in data.items():
        setattr(user, k, v)
    await db.flush()
    await db.refresh(user)
    return user


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: UUID,
    current: CurrentUser = Depends(require_roles("super_admin", "admin")),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if user.id == current.id:
        raise HTTPException(status_code=400, detail="Não é possível excluir a si mesmo")
    await db.delete(user)
    return {"status": "ok"}
