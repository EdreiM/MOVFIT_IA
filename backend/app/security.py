from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from cryptography.fernet import Fernet
from jose import JWTError, jwt
from passlib.context import CryptContext
import base64
import hashlib
import secrets

from app.config import get_settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
settings = get_settings()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_token(data: dict[str, Any], expires_delta: timedelta) -> str:
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + expires_delta
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def create_access_token(user_id: UUID, role: str, company_id: UUID | None = None) -> str:
    data: dict[str, Any] = {"sub": str(user_id), "role": role, "type": "access"}
    if company_id:
        data["company_id"] = str(company_id)
    return create_token(data, timedelta(minutes=settings.access_token_expire_minutes))


def create_refresh_token(user_id: UUID) -> str:
    return create_token(
        {"sub": str(user_id), "type": "refresh"},
        timedelta(days=settings.refresh_token_expire_days),
    )


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.secret_key, algorithms=["HS256"])


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.encryption_key.encode()).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()


def mask_api_key(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}...{value[-4:]}"


API_KEY_PREFIX = "mvk_"


def generate_api_key() -> tuple[str, str, str]:
    """Gera uma chave de acesso pra sistema externo. Retorna
    (chave_completa, prefixo_pra_exibir, hash_pra_guardar) — a chave
    completa só existe aqui, nunca é persistida (só o hash), então precisa
    ser mostrada ao usuário nesse exato momento ou se perde pra sempre."""
    raw = secrets.token_urlsafe(32)
    full_key = f"{API_KEY_PREFIX}{raw}"
    return full_key, full_key[: len(API_KEY_PREFIX) + 8], hash_api_key(full_key)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()
