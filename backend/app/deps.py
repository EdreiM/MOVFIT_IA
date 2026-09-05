from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Company, User, UserCompany
from app.security import decode_token

security = HTTPBearer(auto_error=False)


class CurrentUser:
    def __init__(self, user: User, company_id: UUID | None = None):
        self.user = user
        self.company_id = company_id

    @property
    def id(self) -> UUID:
        return self.user.id

    @property
    def role(self) -> str:
        return self.user.role

    @property
    def is_super_admin(self) -> bool:
        return self.user.role == "super_admin"


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
) -> CurrentUser:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Não autenticado")
    try:
        payload = decode_token(credentials.credentials)
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Token inválido")
        user_id = UUID(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Token inválido")

    result = await db.execute(
        select(User).options(selectinload(User.companies)).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user or user.status != "active":
        raise HTTPException(status_code=401, detail="Usuário inativo ou inexistente")
    if user.role == "atendente":
        raise HTTPException(status_code=403, detail="Acesso ao painel não permitido para atendentes")

    company_id: UUID | None = None
    if x_company_id:
        try:
            company_id = UUID(x_company_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="X-Company-Id inválido")

        if user.role != "super_admin":
            allowed = {uc.company_id for uc in user.companies}
            if company_id not in allowed:
                raise HTTPException(status_code=403, detail="Sem acesso a esta empresa")
    elif user.role != "super_admin" and user.companies:
        company_id = user.companies[0].company_id

    return CurrentUser(user=user, company_id=company_id)


def require_roles(*roles: str):
    async def _dep(current: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current.role not in roles and current.role != "super_admin":
            raise HTTPException(status_code=403, detail="Permissão insuficiente")
        return current

    return _dep


async def resolve_company_id(current: CurrentUser, db: AsyncSession) -> UUID:
    if current.company_id:
        return current.company_id
    if current.is_super_admin:
        result = await db.execute(select(Company).order_by(Company.created_at.asc()).limit(1))
        company = result.scalar_one_or_none()
        if company:
            return company.id
    raise HTTPException(status_code=400, detail="Informe X-Company-Id")
