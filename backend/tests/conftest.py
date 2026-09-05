"""Setup dos testes: banco de teste isolado (monica_test), separado do
banco de desenvolvimento/produção, recriado do zero a cada rodada."""
import os
import uuid

# Precisa vir ANTES de qualquer import de app.* — app/database.py cria a
# engine no momento do import, usando esse valor.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://monica:monica@db:5432/monica_test"
)

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.database as database_module
from app.database import Base
from app.security import hash_password

ADMIN_DB_URL = os.environ.get(
    "TEST_ADMIN_DATABASE_URL", "postgresql+asyncpg://monica:monica@db:5432/monica"
)
TEST_DB_NAME = "monica_test"

# pytest-asyncio (modo strict) usa um event loop novo por teste. Uma engine
# com pool normal guarda conexões presas ao loop em que nasceram — na 2ª
# rodada, a conexão reaproveitada "pertence" a um loop já fechado e explode.
# NullPool abre uma conexão nova a cada uso, sem reaproveitar nada entre
# loops, então esse problema simplesmente não existe.
database_module.engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
database_module.AsyncSessionLocal = async_sessionmaker(
    database_module.engine, expire_on_commit=False
)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _prepare_database():
    admin_engine = create_async_engine(ADMIN_DB_URL, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    async with admin_engine.connect() as conn:
        await conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB_NAME}"))
        await conn.execute(text(f"CREATE DATABASE {TEST_DB_NAME}"))
    await admin_engine.dispose()

    async with database_module.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield

    await database_module.engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables():
    """Limpa as tabelas entre testes — mais rápido que recriar o schema."""
    yield

    # O webhook inbound agenda uma task de resposta com debounce (alguns
    # segundos de silêncio) — sem cancelar, ela fica pendente depois que o
    # teste (e o event loop dele) já terminou.
    from app.services.debounce import _pending_replies

    for task in list(_pending_replies.values()):
        task.cancel()
    _pending_replies.clear()

    async with database_module.engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())


@pytest_asyncio.fixture
async def db_session():
    async with database_module.AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def client():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def company(db_session):
    from app.models import Company

    c = Company(name="Empresa Teste", slug=f"teste-{uuid.uuid4().hex[:8]}", plan="starter", status="active")
    db_session.add(c)
    await db_session.commit()
    await db_session.refresh(c)
    return c


@pytest_asyncio.fixture
async def user(db_session, company):
    from app.models import User, UserCompany

    u = User(
        email="teste@movfit.com",
        hashed_password=hash_password("senha-teste-123"),
        full_name="Usuário Teste",
        role="admin",
        status="active",
    )
    db_session.add(u)
    await db_session.flush()
    db_session.add(UserCompany(user_id=u.id, company_id=company.id))
    await db_session.commit()
    await db_session.refresh(u)
    return u
