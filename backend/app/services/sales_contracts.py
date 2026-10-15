"""Contratos de venda nova que chegam pelo n8n.

O fluxo do n8n guarda os tokens das unidades, consulta
`faturamento-sintetico/resumo-pessoa` da Pacto (uma chamada por unidade e por
produto: matrícula e rematrícula) e manda o resultado pra cá
(`POST /v1/sales/contracts`, chave de API da empresa). Aqui só se normaliza e
se grava — as regras do ranking (anual, responsável, "site") ficam em
sales_ranking.py, que lê dessa tabela.

Cada chamada traz o período inteiro de UM produto de UMA unidade; quando a
resposta veio completa, o que estava gravado nessa janela e não veio de novo
(contrato cancelado, por exemplo) é removido — assim o painel acompanha a Pacto.
"""
import unicodedata
from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SalesContract

_TZ = ZoneInfo("America/Sao_Paulo")

# Só venda NOVA entra aqui; renovação nunca é gravada.
NEW_SALE_PRODUCTS = {"matricula": "Matrícula", "rematricula": "Rematrícula"}
MAX_CONTRACTS_PER_REQUEST = 5000


def norm(text: str | None) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(plain.lower().split())


def canonical_product(value: str | None) -> str | None:
    """"MATRÍCULA" / "Rematrícula" → rótulo padrão; None se não for venda nova."""
    return NEW_SALE_PRODUCTS.get(norm(value))


def is_annual(duracao: int | None, plano: str | None) -> bool:
    """Anual = duração do contrato >= 12 meses. Sem duração, cai pro nome do plano."""
    if duracao is not None:
        return duracao >= 12
    return "anual" in norm(plano)


def _parse_lancamento(value) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        if len(text) <= 10:
            return date.fromisoformat(text)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.date()
    # A Pacto manda o dia como meia-noite de Brasília em UTC (03:00Z).
    return parsed.astimezone(_TZ).date()


def _parse_datetime(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_TZ)


def _text(value, limit: int) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text[:limit] or None


def _first_name(value) -> str | None:
    if isinstance(value, list):
        value = next((v for v in value if v), None)
    return _text(value, 255)


def normalize_record(record: dict, unidade: str, produto: str) -> dict | None:
    """Um registro cru da Pacto → colunas de SalesContract (None = inutilizável)."""
    try:
        contrato = int(record.get("contrato"))
    except (TypeError, ValueError):
        return None
    lancamento = _parse_lancamento(record.get("lancamento")) or _parse_lancamento(record.get("dataHoraVenda"))
    if lancamento is None:
        return None
    try:
        duracao = int(record["duracao"]) if record.get("duracao") is not None else None
    except (TypeError, ValueError):
        duracao = None
    inicio = _parse_lancamento(record.get("dataInicio"))
    return {
        "unidade": unidade,
        "empresa_nome": _text(record.get("empresa"), 255) or unidade,
        "contrato": contrato,
        "matricula": _text(record.get("matricula"), 32),
        "cliente_nome": _text(record.get("nome"), 255),
        "responsavel": _text(record.get("responsavelVenda"), 255),
        "consultor": _first_name(record.get("consultorResponsavel")),
        "plano": _text(record.get("plano"), 255),
        "duracao": duracao,
        "produto": produto,
        "lancamento": lancamento,
        "vendido_em": _parse_datetime(record.get("dataHoraVenda")),
        "inicio": inicio,
    }


async def ingest(
    db: AsyncSession,
    company_id: UUID,
    *,
    unidade: str,
    produto: str,
    data_inicio: date,
    data_fim: date,
    total: int | None,
    records: list[dict],
) -> dict:
    """Grava os contratos de um produto/unidade/período. Não dá commit."""
    label = canonical_product(produto)
    if label is None:
        raise ValueError("Só matrícula e rematrícula são aceitas (renovação não conta como venda nova).")
    unidade = unidade.strip()

    rows: dict[int, dict] = {}
    ignored = 0
    for record in records:
        row = normalize_record(record, unidade, label) if isinstance(record, dict) else None
        if row is None:
            ignored += 1
            continue
        rows[row["contrato"]] = row

    if rows:
        stmt = pg_insert(SalesContract).values([{"company_id": company_id, **row} for row in rows.values()])
        update_cols = {
            col: stmt.excluded[col]
            for col in (
                "empresa_nome", "matricula", "cliente_nome", "responsavel", "consultor", "plano",
                "duracao", "produto", "lancamento", "vendido_em", "inicio",
            )
        }
        stmt = stmt.on_conflict_do_update(
            constraint="uq_sales_contracts_key", set_={**update_cols, "synced_at": func.now()}
        )
        await db.execute(stmt)

    removed = 0
    complete = total is None or len(records) >= total
    if complete:
        existing = (
            await db.execute(
                select(SalesContract.id, SalesContract.contrato, SalesContract.produto).where(
                    SalesContract.company_id == company_id,
                    SalesContract.unidade == unidade,
                    SalesContract.lancamento >= data_inicio,
                    SalesContract.lancamento <= data_fim,
                )
            )
        ).all()
        stale = [row_id for row_id, contrato, prod in existing if norm(prod) == norm(label) and contrato not in rows]
        if stale:
            await db.execute(delete(SalesContract).where(SalesContract.id.in_(stale)))
            removed = len(stale)
    return {"gravados": len(rows), "removidos": removed, "ignorados": ignored, "completo": complete}


async def covered_unit_names(db: AsyncSession, company_id: UUID) -> dict[str, str]:
    """{nome normalizado da empresa: nome como a Pacto escreve} das unidades
    que já têm contratos vindos do n8n — pra elas a coleta pelo MCP é dispensável."""
    result = await db.execute(
        select(SalesContract.empresa_nome).where(SalesContract.company_id == company_id).distinct()
    )
    return {norm(name): name for (name,) in result.all() if name}
