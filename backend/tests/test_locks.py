"""advisory_lock: o lock tem que ser solto mesmo quando o chamador dá commit
dentro do bloco e a conexão dele volta pro pool."""
import os

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services.locks import advisory_lock

NAMESPACE = 9901
KEY = 42


@pytest.mark.asyncio
async def test_lock_is_released_after_caller_commits_inside_the_block():
    # Pool de verdade (os outros testes usam NullPool, que fecha a conexão a
    # cada uso e por isso nunca reproduz lock preso em conexão ociosa).
    engine = create_async_engine(os.environ["DATABASE_URL"], pool_size=3, max_overflow=0)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as work_db, session_factory() as other_db:
            async with advisory_lock(work_db, NAMESPACE, KEY) as acquired:
                assert acquired
                await work_db.execute(text("select 1"))
                # O commit devolve a conexão do chamador pro pool...
                await work_db.commit()
                # ...e outra sessão (ex: uma requisição do painel) a pega em seguida.
                await other_db.execute(text("select 1"))

            async with engine.connect() as probe:
                still_held = await probe.scalar(
                    select(func.count())
                    .select_from(text("pg_locks"))
                    .where(text("locktype = 'advisory' and classid = :ns and objid = :key"))
                    .params(ns=NAMESPACE, key=KEY)
                )
            assert still_held == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_second_holder_is_refused_while_lock_is_held(db_session):
    async with advisory_lock(db_session, NAMESPACE, KEY) as first:
        assert first
        async with advisory_lock(db_session, NAMESPACE, KEY) as second:
            assert second is False
    async with advisory_lock(db_session, NAMESPACE, KEY) as again:
        assert again
