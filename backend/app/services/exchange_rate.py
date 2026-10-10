"""Cotação do dólar pra mostrar o custo da IA em reais.

Fonte: PTAX de venda do Banco Central (API pública Olinda, sem chave). A
cotação é buscada quando alguém abre o custo no painel e a guardada tem mais
de 12h — não existe job em background. Se o Banco Central não responder,
vale a última cotação guardada: o painel nunca fica sem número por causa
disso.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AiConfig

logger = logging.getLogger(__name__)

_PTAX_URL = (
    "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"
    "CotacaoDolarPeriodo(dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)"
)
_BRAZIL_TZ = ZoneInfo("America/Sao_Paulo")
_MAX_AGE = timedelta(hours=12)
# Depois de uma falha, só tenta de novo passado esse tempo — senão cada
# abertura do painel esperaria o timeout de novo enquanto o BC estiver fora.
_RETRY_AFTER_FAILURE_SECONDS = 30 * 60
_last_failure: dict[UUID, float] = {}


async def fetch_usd_brl_ptax() -> float | None:
    """PTAX de venda mais recente. Fim de semana e feriado não têm cotação,
    então olha os últimos 7 dias e pega a última publicada."""
    today = datetime.now(_BRAZIL_TZ).date()
    params = {
        "@dataInicial": f"'{(today - timedelta(days=7)).strftime('%m-%d-%Y')}'",
        "@dataFinalCotacao": f"'{today.strftime('%m-%d-%Y')}'",
        "$format": "json",
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(_PTAX_URL, params=params)
            resp.raise_for_status()
            quotes = resp.json().get("value") or []
        if not quotes:
            return None
        latest = max(quotes, key=lambda q: q.get("dataHoraCotacao") or "")
        rate = float(latest["cotacaoVenda"])
        return rate if rate > 0 else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Não foi possível buscar a cotação do dólar (PTAX): %s", exc)
        return None


async def refresh_usd_brl_rate(db: AsyncSession, config: AiConfig) -> None:
    """Atualiza `config.usd_brl_rate` pela PTAX quando a cotação automática
    está ligada e a guardada está velha. Não faz nada no modo manual."""
    if not config.usd_brl_rate_auto:
        return
    now = datetime.now(timezone.utc)
    if config.usd_brl_rate_updated_at and now - config.usd_brl_rate_updated_at < _MAX_AGE:
        return
    if time.monotonic() - _last_failure.get(config.company_id, float("-inf")) < _RETRY_AFTER_FAILURE_SECONDS:
        return
    rate = await fetch_usd_brl_ptax()
    if rate is None:
        _last_failure[config.company_id] = time.monotonic()
        return
    _last_failure.pop(config.company_id, None)
    config.usd_brl_rate = round(rate, 4)
    config.usd_brl_rate_updated_at = now
    await db.flush()
