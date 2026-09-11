import logging
from contextlib import asynccontextmanager

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Namespaces fixos pra separar "famílias" de lock (mesmo `key` em famílias
# diferentes não colide). Cada job/tarefa em background novo que precisar de
# lock ganha o próximo número — ver CLAUDE.md, seção "Jobs em background".
LOCK_NAMESPACE_FOLLOWUP_SWEEP = 1
LOCK_NAMESPACE_CONVERSATION_REPLY = 2


@asynccontextmanager
async def advisory_lock(db: AsyncSession, namespace: int, key: int = 0):
    """Lock consultivo do Postgres (pg_advisory_lock/pg_advisory_unlock),
    escopado à sessão — não trava linha nem tabela nenhuma, só coordena
    processos que concordam em respeitar essa trava.

    Existe pra garantir que só UM processo por vez execute um trecho de
    código mesmo que várias cópias do backend estejam rodando ao mesmo
    tempo (hoje roda sempre uma cópia só, mas isso protege o dia em que
    escalar pra múltiplas).

    pg_try_advisory_lock/pg_advisory_unlock têm efeito imediato — não fazem
    parte da transação, não precisam (nem devem) de commit pra "valer".
    Por isso é seguro usar a mesma `db` que o chamador já usa pro resto do
    trabalho: não commitamos nem revertemos nada aqui, quem decide o
    destino da transação continua sendo o chamador.

    Uso:
        async with advisory_lock(db, LOCK_NAMESPACE_X, key) as acquired:
            if not acquired:
                return  # outro processo já está fazendo isso agora
            ...trabalho...
    """
    acquired = bool(await db.scalar(select(func.pg_try_advisory_lock(namespace, key))))
    if not acquired:
        logger.debug("Lock (namespace=%s, key=%s) já está em uso por outro processo.", namespace, key)
    try:
        yield acquired
    except BaseException:
        if acquired:
            try:
                await db.rollback()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Rollback após erro dentro do advisory_lock (namespace=%s, key=%s)",
                    namespace,
                    key,
                )
        raise
    finally:
        if acquired:
            try:
                await db.execute(select(func.pg_advisory_unlock(namespace, key)))
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Falha ao liberar advisory_lock (namespace=%s, key=%s)",
                    namespace,
                    key,
                )


def uuid_lock_key(value) -> int:
    """Dobra um UUID num inteiro de 31 bits estável, pra usar como `key` de
    advisory_lock por conversa/entidade — pg_advisory_lock(int, int) só
    aceita int32. Colisão é possível (raríssima) e inofensiva: na pior
    hipótese, duas entidades diferentes não conseguem processar no mesmíssimo
    instante, uma delas só espera o próximo ciclo."""
    return value.int & 0x7FFFFFFF
