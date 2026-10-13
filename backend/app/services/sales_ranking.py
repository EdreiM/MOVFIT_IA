"""Ranking de vendas: novas matrículas ANUAIS por responsável, por unidade.

Os números vêm do relatório de faturamento da Pacto (a mesma tela "Relatório
Faturamento por Período"), pelo servidor de relatórios que o painel já usa —
ver app/services/pacto.py. Regras (validadas contra a planilha da
supervisora, Santarém 24h, 01–13/10/2026):

* o responsável é quem LANÇOU o contrato (não o "consultor");
* conta só MATRÍCULA nova — rematrícula e renovação não entram;
* conta só plano anual (duração >= 12 meses) — mensal avulso etc. ficam fora.

A Pacto separa "tipo de venda" e "plano" em partes diferentes do relatório,
então o cruzamento "matrícula × plano anual" só fica exato no nível do DIA:
num dia, o responsável tem poucas vendas e dá pra saber qual era a avulsa.
Por isso a coleta é por dia, e a tela soma qualquer período.
"""
import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models import PactoConnection, PactoUnit, SalesDaily, SalesSyncDay
from app.services import pacto
from app.services.locks import LOCK_NAMESPACE_SALES_SYNC, advisory_lock

logger = logging.getLogger(__name__)

_TZ = ZoneInfo("America/Sao_Paulo")
_TIPOS = ["matriculas_renovacoes", "mensalidades"]
_RECENT_DAYS = 3  # hoje + 2 dias anteriores sempre reconferidos (vendas lançadas/canceladas depois)
MAX_CALLS_PER_RUN = 500
SYNC_INTERVAL_SECONDS = 20 * 60
BACKLOG_INTERVAL_SECONDS = 3 * 60  # enquanto o histórico ainda está sendo carregado
UNASSIGNED_NAME = "Não identificado"
MAX_RANGE_DAYS = 366


class _BudgetExceeded(Exception):
    """Estourou o limite de chamadas desta rodada — continua na próxima."""


def _norm(text: str | None) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(plain.lower().split())


_DURATION_RE = re.compile(r"-\s*(\d+)\s*-\s*cod", re.IGNORECASE)


def is_annual_plan(description: str | None) -> bool:
    """"MOVFIT PREMIUM (ANUAL RECORRENTE) - 12 - cod. 33" → True;
    "MOVFIT MENSAL AVULSO - 1 - cod. 35" → False."""
    match = _DURATION_RE.search(description or "")
    if match:
        return int(match.group(1)) >= 12
    return "anual" in _norm(description)


@dataclass
class DayCounts:
    matriculas: int = 0
    rematriculas: int = 0
    renovacoes: int = 0
    plans: list[tuple[str, int]] = field(default_factory=list)

    @property
    def non_annual_total(self) -> int:
        return sum(qty for desc, qty in self.plans if not is_annual_plan(desc))


def parse_report(report: dict) -> DayCounts:
    """Lê o retorno de relatorio_faturamento_empresa."""
    counts = DayCounts()
    for group in report.get("por_tipo_produto") or []:
        group_name = _norm(group.get("tipo_produto"))
        is_sale_types = "matricula" in group_name and "renov" in group_name
        for product in group.get("produtos") or []:
            description = str(product.get("descricao") or "")
            quantity = int(product.get("quantidade") or 0)
            if is_sale_types:
                kind = _norm(description)
                if kind.startswith("rematricula"):
                    counts.rematriculas += quantity
                elif kind.startswith("renov"):
                    counts.renovacoes += quantity
                elif kind.startswith("matricula"):
                    counts.matriculas += quantity
            else:
                counts.plans.append((description, quantity))
    return counts


class _Budget:
    def __init__(self, max_calls: int):
        self.max_calls = max_calls
        self.used = 0

    def spend(self) -> None:
        self.used += 1
        if self.used > self.max_calls:
            raise _BudgetExceeded()


@dataclass
class _Row:
    responsavel_id: int
    nome: str
    counts: DayCounts
    na: int = 0
    estimated: bool = False


async def _report(
    session: pacto.PactoSession, budget: _Budget, unit: PactoUnit, day: date, responsavel_id: int | None = None
) -> DayCounts:
    budget.spend()
    args: dict = {
        "chave_empresa": unit.chave_empresa,
        "empresa_id": unit.empresa_id,
        "data_inicio": day.isoformat(),
        "data_termino": day.isoformat(),
        "tipos_produto": _TIPOS,
    }
    if responsavel_id is not None:
        args["responsavel_id"] = responsavel_id
    return parse_report(await session.call_tool("relatorio_faturamento_empresa", args))


async def _non_annual_matriculas(
    session: pacto.PactoSession, budget: _Budget, unit: PactoUnit, day: date, plans: list[tuple[str, int]]
) -> int:
    """Quantas das vendas de plano NÃO anual do dia foram matrícula nova
    (a lista venda a venda informa o tipo de cada uma)."""
    total = 0
    for description, quantity in plans:
        if quantity <= 0 or is_annual_plan(description):
            continue
        budget.spend()
        result = await session.call_tool(
            "vendas_produto_faturamento_empresa",
            {
                "chave_empresa": unit.chave_empresa,
                "empresa_id": unit.empresa_id,
                "produto": description,
                "data_inicio": day.isoformat(),
                "data_termino": day.isoformat(),
                "tipos_produto": _TIPOS,
                "tamanho": 200,
            },
        )
        for sale in result.get("vendas") or []:
            if _norm(sale.get("tipo_venda")).startswith("matricula"):
                total += 1
    return total


def allocate_non_annual(rows: list[_Row], na_unit_matriculas: int) -> None:
    """Descobre, por responsável, quantas matrículas do dia foram de plano não
    anual. Exato quando o responsável só teve matrículas no dia; estimado
    (limitado pelo total da unidade) quando misturou com rematrícula/renovação."""
    budget = na_unit_matriculas
    pending: list[_Row] = []
    for row in rows:
        c = row.counts
        na_sales = c.non_annual_total
        if na_sales == 0:
            row.na = 0
        elif c.rematriculas == 0 and c.renovacoes == 0:
            row.na = min(na_sales, c.matriculas)
            budget -= row.na
        else:
            pending.append(row)
    for row in pending:
        c = row.counts
        row.na = max(min(c.non_annual_total, c.matriculas, budget), 0)
        row.estimated = True
        budget -= row.na


async def _known_sellers(db: AsyncSession, company_id: UUID, unit: PactoUnit, today: date) -> list[tuple[int, str]]:
    since = today - timedelta(days=120)
    result = await db.execute(
        select(SalesDaily.responsavel_id, func.max(SalesDaily.responsavel_nome), func.sum(SalesDaily.matriculas))
        .where(
            SalesDaily.company_id == company_id,
            SalesDaily.chave_empresa == unit.chave_empresa,
            SalesDaily.empresa_id == unit.empresa_id,
            SalesDaily.day >= since,
            SalesDaily.responsavel_id != 0,
        )
        .group_by(SalesDaily.responsavel_id)
        .order_by(func.sum(SalesDaily.matriculas).desc())
    )
    return [(int(rid), str(nome)) for rid, nome, _total in result.all()]


async def _all_responsaveis(session: pacto.PactoSession, budget: _Budget, unit: PactoUnit) -> list[tuple[int, str]]:
    budget.spend()
    data = await session.call_tool(
        "listar_responsaveis_lancamento_empresa",
        {"chave_empresa": unit.chave_empresa, "empresa_id": unit.empresa_id},
    )
    return [
        (int(r["responsavel_id"]), str(r.get("nome") or ""))
        for r in data.get("responsaveis") or []
        if r.get("responsavel_id") is not None
    ]


async def sync_unit_day(
    db: AsyncSession,
    session: pacto.PactoSession,
    budget: _Budget,
    company_id: UUID,
    unit: PactoUnit,
    day: date,
    sellers: list[tuple[int, str]],
    responsaveis_cache: dict[str, list[tuple[int, str]]],
) -> None:
    unit_counts = await _report(session, budget, unit, day)
    total = unit_counts.matriculas

    previous = await db.scalar(
        select(SalesSyncDay).where(
            SalesSyncDay.company_id == company_id,
            SalesSyncDay.chave_empresa == unit.chave_empresa,
            SalesSyncDay.empresa_id == unit.empresa_id,
            SalesSyncDay.day == day,
        )
    )
    if previous is not None and previous.matriculas_total == total:
        previous.synced_at = datetime.now(timezone.utc)  # nada mudou: só marca como conferido
        return

    rows: list[_Row] = []
    if total > 0:
        tried: set[int] = set()
        attributed = 0

        async def attribute(responsavel_id: int, nome: str) -> None:
            nonlocal attributed
            tried.add(responsavel_id)
            counts = await _report(session, budget, unit, day, responsavel_id)
            if counts.matriculas or counts.rematriculas or counts.renovacoes:
                rows.append(_Row(responsavel_id, nome, counts))
                attributed += counts.matriculas
                if (responsavel_id, nome) not in sellers:
                    sellers.append((responsavel_id, nome))

        for responsavel_id, nome in list(sellers):
            if attributed >= total:
                break
            await attribute(responsavel_id, nome)
        if attributed < total:
            cache_key = f"{unit.chave_empresa}:{unit.empresa_id}"
            if cache_key not in responsaveis_cache:
                responsaveis_cache[cache_key] = await _all_responsaveis(session, budget, unit)
            for responsavel_id, nome in responsaveis_cache[cache_key]:
                if attributed >= total:
                    break
                if responsavel_id not in tried:
                    await attribute(responsavel_id, nome)

        na_unit = 0
        if unit_counts.non_annual_total > 0:
            na_unit = await _non_annual_matriculas(session, budget, unit, day, unit_counts.plans)
        allocate_non_annual(rows, na_unit)

        if attributed < total:
            # Vendas que a Pacto contou e nenhum responsável conhecido reivindicou.
            leftover = total - attributed
            used_na = sum(r.na for r in rows)
            rows.append(
                _Row(
                    0,
                    UNASSIGNED_NAME,
                    DayCounts(matriculas=leftover),
                    na=max(min(leftover, na_unit - used_na), 0),
                    estimated=True,
                )
            )

    await db.execute(
        delete(SalesDaily).where(
            SalesDaily.company_id == company_id,
            SalesDaily.chave_empresa == unit.chave_empresa,
            SalesDaily.empresa_id == unit.empresa_id,
            SalesDaily.day == day,
        )
    )
    for row in rows:
        db.add(
            SalesDaily(
                company_id=company_id,
                chave_empresa=unit.chave_empresa,
                empresa_id=unit.empresa_id,
                day=day,
                responsavel_id=row.responsavel_id,
                responsavel_nome=row.nome[:255],
                matriculas=row.counts.matriculas,
                novas_anuais=max(row.counts.matriculas - row.na, 0),
                rematriculas=row.counts.rematriculas,
                renovacoes=row.counts.renovacoes,
                estimated=row.estimated,
            )
        )
    if previous is None:
        db.add(
            SalesSyncDay(
                company_id=company_id,
                chave_empresa=unit.chave_empresa,
                empresa_id=unit.empresa_id,
                day=day,
                matriculas_total=total,
            )
        )
    else:
        previous.matriculas_total = total
        previous.synced_at = datetime.now(timezone.utc)
    await db.flush()


async def _synced_days(db: AsyncSession, company_id: UUID, unit: PactoUnit, since: date) -> set[date]:
    result = await db.execute(
        select(SalesSyncDay.day).where(
            SalesSyncDay.company_id == company_id,
            SalesSyncDay.chave_empresa == unit.chave_empresa,
            SalesSyncDay.empresa_id == unit.empresa_id,
            SalesSyncDay.day >= since,
        )
    )
    return {d for (d,) in result.all()}


async def sync_company(db: AsyncSession, connection: PactoConnection, max_calls: int = MAX_CALLS_PER_RUN) -> bool:
    """Uma rodada de coleta. Devolve True se ainda há histórico a carregar."""
    company_id = connection.company_id
    token = await pacto._valid_access_token(db, connection)
    units_query = select(PactoUnit).where(PactoUnit.company_id == company_id).order_by(PactoUnit.pacto_name)
    units = (await db.execute(units_query)).scalars().all()
    if not units:
        # Conectou, mas a descoberta de unidades não tinha dado certo (servidor
        # da Pacto fora do ar na hora): tenta agora, em vez de ficar parado.
        await pacto.discover_units(db, connection)
        await db.commit()
        units = (await db.execute(units_query)).scalars().all()
    today = datetime.now(_TZ).date()
    horizon = get_settings().sales_backfill_days
    synced = {u.id: await _synced_days(db, company_id, u, today - timedelta(days=horizon)) for u in units}
    sellers = {u.id: await _known_sellers(db, company_id, u, today) for u in units}
    responsaveis_cache: dict[str, list[tuple[int, str]]] = {}
    budget = _Budget(max_calls)
    pending = False

    async with pacto.PactoSession(token, timeout=60.0) as session:
        # Do dia mais novo pro mais antigo, uma unidade de cada vez: todas as
        # unidades ganham o histórico recente antes de qualquer uma ir fundo.
        for offset in range(horizon):
            day = today - timedelta(days=offset)
            for unit in units:
                if offset >= _RECENT_DAYS and day in synced[unit.id]:
                    continue
                try:
                    await sync_unit_day(
                        db, session, budget, company_id, unit, day, sellers[unit.id], responsaveis_cache
                    )
                    await db.commit()
                except _BudgetExceeded:
                    await db.rollback()
                    return True
                except pacto.PactoError as exc:
                    await db.rollback()
                    logger.warning("Vendas: falha em %s dia %s: %s", unit.pacto_name, day, exc)
                    pending = True
    return pending


# --- Rotina em background -------------------------------------------------

_running_companies: set[UUID] = set()


async def run_sales_sync(only_company: UUID | None = None) -> bool:
    """Roda uma rodada pra cada empresa conectada. True = ainda há o que carregar.
    Lock consultivo: só uma cópia do backend coleta por vez (CLAUDE.md,
    "Jobs em background")."""
    pending = False
    async with AsyncSessionLocal() as db:
        async with advisory_lock(db, LOCK_NAMESPACE_SALES_SYNC) as acquired:
            if not acquired:
                return False
            query = select(PactoConnection).where(PactoConnection.status == "connected")
            if only_company is not None:
                query = query.where(PactoConnection.company_id == only_company)
            for connection in (await db.execute(query)).scalars().all():
                try:
                    if await sync_company(db, connection):
                        pending = True
                except pacto.PactoError as exc:
                    logger.warning("Vendas: coleta da empresa %s falhou: %s", connection.company_id, exc)
                    await db.commit()  # _valid_access_token pode ter marcado a conexão com erro
                except Exception:  # noqa: BLE001
                    logger.exception("Vendas: erro inesperado na empresa %s", connection.company_id)
                    await db.rollback()
    return pending


async def trigger_sync(company_id: UUID) -> bool:
    """Dispara uma rodada em segundo plano (botão "Atualizar agora"). False se
    já há uma rodando pra essa empresa."""
    if company_id in _running_companies:
        return False
    _running_companies.add(company_id)

    async def _job() -> None:
        try:
            await run_sales_sync(company_id)
        except Exception:  # noqa: BLE001
            logger.exception("Vendas: falha na atualização manual")
        finally:
            _running_companies.discard(company_id)

    asyncio.create_task(_job())
    return True


def is_syncing(company_id: UUID) -> bool:
    return company_id in _running_companies


async def periodic_sales_sync_loop() -> None:
    """Roda pra sempre em background (ver lifespan em app/main.py). Enquanto
    há histórico a carregar, volta logo; depois, a cada 20 minutos."""
    await asyncio.sleep(60)  # deixa o backend terminar de subir
    while True:
        delay = SYNC_INTERVAL_SECONDS
        try:
            if await run_sales_sync():
                delay = BACKLOG_INTERVAL_SECONDS
        except Exception:  # noqa: BLE001
            logger.exception("Vendas: falha na coleta periódica")
        await asyncio.sleep(delay)


# --- Consulta pra tela ---------------------------------------------------


def _site_names() -> set[str]:
    return {_norm(n) for n in get_settings().sales_site_names.split(",") if n.strip()}


_NAME_PARTICLES = {"da", "de", "do", "das", "dos", "e"}


def display_name(name: str) -> str:
    """"PABLO SILVA DA ROCHA" → "Pablo Silva da Rocha"."""
    words = (name or "").lower().split()
    return " ".join(w if (i > 0 and w in _NAME_PARTICLES) else w.capitalize() for i, w in enumerate(words))


def unit_key(unit: PactoUnit) -> str:
    return f"{unit.chave_empresa}:{unit.empresa_id}"


async def build_ranking(
    db: AsyncSession, company_id: UUID, unit: str | None, date_from: date, date_to: date
) -> dict:
    units = (
        (await db.execute(select(PactoUnit).where(PactoUnit.company_id == company_id).order_by(PactoUnit.pacto_name)))
        .scalars()
        .all()
    )
    by_key = {unit_key(u): u for u in units}
    selected = [by_key[unit]] if unit and unit != "all" and unit in by_key else list(units)
    pairs = [(u.chave_empresa, u.empresa_id) for u in selected]

    rows = []
    synced_count = 0
    last_synced: datetime | None = None
    if selected:
        pair_filter = [
            (SalesDaily.chave_empresa == c) & (SalesDaily.empresa_id == e) for c, e in pairs
        ]
        rows = (
            (
                await db.execute(
                    select(SalesDaily).where(
                        SalesDaily.company_id == company_id,
                        SalesDaily.day >= date_from,
                        SalesDaily.day <= date_to,
                        or_(*pair_filter),
                    )
                )
            )
            .scalars()
            .all()
        )
        sync_filter = [
            (SalesSyncDay.chave_empresa == c) & (SalesSyncDay.empresa_id == e) for c, e in pairs
        ]
        sync_rows = (
            await db.execute(
                select(func.count(), func.max(SalesSyncDay.synced_at)).where(
                    SalesSyncDay.company_id == company_id,
                    SalesSyncDay.day >= date_from,
                    SalesSyncDay.day <= date_to,
                    or_(*sync_filter),
                )
            )
        ).one()
        synced_count, last_synced = int(sync_rows[0] or 0), sync_rows[1]

    today = datetime.now(_TZ).date()
    effective_to = min(date_to, today)
    days_in_range = max((effective_to - date_from).days + 1, 0)
    expected = days_in_range * len(selected)

    site = _site_names()
    people: dict[str, dict] = {}
    site_total = unassigned = team_total = matriculas_all = 0
    estimated_any = False
    for r in rows:
        matriculas_all += r.matriculas
        estimated_any = estimated_any or r.estimated
        name_key = _norm(r.responsavel_nome)
        if r.responsavel_id == 0:
            unassigned += r.novas_anuais
            continue
        if name_key in site:
            site_total += r.novas_anuais
            continue
        team_total += r.novas_anuais
        entry = people.setdefault(
            name_key,
            {"nome": display_name(r.responsavel_nome), "novas_anuais": 0, "matriculas": 0, "estimated": False},
        )
        entry["novas_anuais"] += r.novas_anuais
        entry["matriculas"] += r.matriculas
        entry["estimated"] = entry["estimated"] or r.estimated

    ranking = sorted(
        (p for p in people.values() if p["novas_anuais"] > 0),
        key=lambda p: (-p["novas_anuais"], p["nome"]),
    )
    for position, person in enumerate(ranking, start=1):
        person["posicao"] = position

    return {
        "periodo": {"de": date_from.isoformat(), "ate": date_to.isoformat()},
        "unidade": unit if unit in by_key else "all",
        "unidades": [{"key": unit_key(u), "nome": u.pacto_name} for u in units],
        "total_novas_anuais": team_total + site_total + unassigned,
        "equipe": team_total,
        "site": site_total,
        "nao_identificado": unassigned,
        "matriculas_todas": matriculas_all,
        "ranking": ranking,
        "aproximado": estimated_any,
        "dias_pendentes": max(expected - synced_count, 0),
        "atualizado_em": last_synced.isoformat() if last_synced else None,
    }
