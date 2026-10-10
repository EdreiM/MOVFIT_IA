"""Custo da IA: captura dos tokens de cada chamada ao modelo, cálculo do
custo e os números do painel.

A captura é por um "coletor" em contextvar: quem vai chamar o modelo abre
`collect_llm_usage()`, as chamadas feitas dentro (em qualquer profundidade)
anotam o consumo, e no fim quem abriu grava com `record_llm_usage`. Assim
`chat_completion` continua devolvendo só a mensagem — nenhum chamador nem
teste precisou mudar.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Lead, LlmUsage

logger = logging.getLogger(__name__)

# Dólares por 1 milhão de tokens: (entrada, entrada em cache, saída).
# Preço padrão da OpenAI, conferido em developers.openai.com/api/docs/pricing
# em 10/10/2026. Ao mudar aqui, só as chamadas NOVAS usam o preço novo — o
# custo de cada linha de llm_usage é gravado na hora.
PRICES_USD_PER_MTOK: dict[str, tuple[Decimal, Decimal, Decimal]] = {
    "gpt-4o-mini": (Decimal("0.15"), Decimal("0.075"), Decimal("0.60")),
    "gpt-4o": (Decimal("2.50"), Decimal("1.25"), Decimal("10.00")),
    "gpt-4.1-mini": (Decimal("0.40"), Decimal("0.10"), Decimal("1.60")),
    "gpt-4.1": (Decimal("2.00"), Decimal("0.50"), Decimal("8.00")),
    "o4-mini": (Decimal("1.10"), Decimal("0.275"), Decimal("4.40")),
}
_MTOK = Decimal(1_000_000)
_BRAZIL_TZ = "America/Sao_Paulo"

_collector: ContextVar[list[dict] | None] = ContextVar("llm_usage_collector", default=None)


@contextmanager
def collect_llm_usage():
    """Junta o consumo das chamadas ao modelo feitas dentro do bloco."""
    bucket: list[dict] = []
    token = _collector.set(bucket)
    try:
        yield bucket
    finally:
        _collector.reset(token)


def note_llm_usage(model: str, usage: dict | None) -> None:
    """Chamado por quem fala com o provedor, com o `usage` da resposta."""
    bucket = _collector.get()
    if bucket is None or not isinstance(usage, dict):
        return
    details = usage.get("prompt_tokens_details") or {}
    bucket.append(
        {
            "model": model,
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "cached_tokens": int(details.get("cached_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
        }
    )


def _price_for(model: str) -> tuple[Decimal, Decimal, Decimal] | None:
    """Preço do modelo. A API devolve o nome com data ("gpt-4o-mini-2024-07-18"),
    então vale o nome cadastrado mais longo que seja prefixo dele — "gpt-4o-mini"
    e não "gpt-4o"."""
    matches = [name for name in PRICES_USD_PER_MTOK if model == name or model.startswith(f"{name}-")]
    return PRICES_USD_PER_MTOK[max(matches, key=len)] if matches else None


def cost_usd(model: str, prompt_tokens: int, cached_tokens: int, completion_tokens: int) -> Decimal | None:
    price = _price_for(model or "")
    if price is None:
        return None
    input_price, cached_price, output_price = price
    cached = min(cached_tokens, prompt_tokens)
    return (
        (prompt_tokens - cached) * input_price + cached * cached_price + completion_tokens * output_price
    ) / _MTOK


async def record_llm_usage(
    db: AsyncSession, conversation: Conversation, bucket: list[dict], purpose: str
) -> None:
    for item in bucket:
        cost = cost_usd(item["model"], item["prompt_tokens"], item["cached_tokens"], item["completion_tokens"])
        if cost is None:
            logger.warning("Modelo %r sem preço cadastrado — custo não calculado.", item["model"])
        db.add(
            LlmUsage(
                company_id=conversation.company_id,
                conversation_id=conversation.id,
                model=item["model"],
                purpose=purpose,
                prompt_tokens=item["prompt_tokens"],
                cached_tokens=item["cached_tokens"],
                completion_tokens=item["completion_tokens"],
                cost_usd=cost,
            )
        )
    if bucket:
        await db.flush()


def _month_column():
    # Constantes como literal (não parâmetro): a mesma expressão aparece no
    # SELECT e no GROUP BY, e com parâmetros o Postgres as trata como diferentes.
    return func.to_char(
        func.timezone(literal_column(f"'{_BRAZIL_TZ}'"), LlmUsage.created_at), literal_column("'YYYY-MM'")
    )


async def compute_ai_cost(
    db: AsyncSession, company_id: UUID, *, month: str | None, usd_brl_rate: float
) -> dict:
    """Custo da IA de uma empresa. `month` = "AAAA-MM" (fuso de Brasília) ou
    None pra tudo. Valores em dólar (como o provedor cobra) e em reais pela
    cotação configurada."""
    rate = Decimal(str(usd_brl_rate))

    def money(usd: Decimal | None) -> dict:
        usd = usd or Decimal(0)
        return {"usd": float(round(usd, 4)), "brl": float(round(usd * rate, 2))}

    month_col = _month_column()
    by_month_rows = (
        await db.execute(
            select(month_col, func.coalesce(func.sum(LlmUsage.cost_usd), 0), func.count())
            .where(LlmUsage.company_id == company_id)
            .group_by(month_col)
            .order_by(month_col)
        )
    ).all()
    by_month = [{"month": m, "calls": calls, **money(total)} for m, total, calls in by_month_rows]

    scope = [LlmUsage.company_id == company_id]
    if month:
        scope.append(month_col == month)
    is_test = Conversation.channel == "test_console"

    totals = (
        await db.execute(
            select(
                func.coalesce(func.sum(LlmUsage.cost_usd), 0),
                func.count(),
                func.coalesce(func.sum(LlmUsage.prompt_tokens), 0),
                func.coalesce(func.sum(LlmUsage.completion_tokens), 0),
                func.count().filter(LlmUsage.cost_usd.is_(None)),
            ).where(*scope)
        )
    ).one()
    test_chat_usd = await db.scalar(
        select(func.coalesce(func.sum(LlmUsage.cost_usd), 0))
        .join(Conversation, Conversation.id == LlmUsage.conversation_id)
        .where(*scope, is_test)
    )

    by_model_rows = (
        await db.execute(
            select(LlmUsage.model, func.coalesce(func.sum(LlmUsage.cost_usd), 0), func.count())
            .where(*scope)
            .group_by(LlmUsage.model)
            .order_by(func.coalesce(func.sum(LlmUsage.cost_usd), 0).desc())
        )
    ).all()

    # Custo por cliente = por telefone, somando todas as conversas dele.
    customer_rows = (
        await db.execute(
            select(
                Conversation.contact_phone,
                func.max(Conversation.contact_name),
                func.coalesce(func.sum(LlmUsage.cost_usd), 0).label("total"),
                func.count(),
                func.max(LlmUsage.created_at),
            )
            .join(Conversation, Conversation.id == LlmUsage.conversation_id)
            .where(*scope, ~is_test)
            .group_by(Conversation.contact_phone)
            .order_by(func.coalesce(func.sum(LlmUsage.cost_usd), 0).desc())
        )
    ).all()
    lead_names = {}
    if customer_rows:
        digits = {"".join(ch for ch in (phone or "") if ch.isdigit()): phone for phone, *_ in customer_rows}
        leads = await db.execute(
            select(Lead.phone, Lead.name).where(Lead.company_id == company_id, Lead.phone.in_(list(digits)))
        )
        lead_names = {digits[phone]: name for phone, name in leads.all() if name}

    customers_total = sum((row.total for row in customer_rows), Decimal(0))
    customers = [
        {
            "phone": phone,
            "name": lead_names.get(phone) or contact_name,
            "calls": calls,
            "last_at": last_at,
            **money(total),
        }
        for phone, contact_name, total, calls, last_at in customer_rows[:50]
    ]
    average = customers_total / len(customer_rows) if customer_rows else Decimal(0)

    return {
        "month": month,
        "usd_brl_rate": float(rate),
        "total": money(totals[0]),
        "test_chat": money(test_chat_usd),
        "calls": totals[1],
        "prompt_tokens": int(totals[2]),
        "completion_tokens": int(totals[3]),
        "calls_without_price": totals[4],
        "customers_count": len(customer_rows),
        "average_per_customer": money(average),
        "by_month": by_month,
        "by_model": [{"model": m, "calls": calls, **money(total)} for m, total, calls in by_model_rows],
        "customers": customers,
    }
