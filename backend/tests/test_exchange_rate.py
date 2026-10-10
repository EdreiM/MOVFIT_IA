"""Cotação automática do dólar (PTAX) usada no custo da IA em reais."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import AiConfig
from app.services import exchange_rate
from app.services.exchange_rate import fetch_usd_brl_ptax, refresh_usd_brl_rate


@pytest.fixture(autouse=True)
def _forget_failures():
    exchange_rate._last_failure.clear()
    yield
    exchange_rate._last_failure.clear()


async def _config(db_session, company, **fields) -> AiConfig:
    config = AiConfig(company_id=company.id, usd_brl_rate=5.0, **fields)
    db_session.add(config)
    await db_session.commit()
    return config


def _ptax_response(payload):
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = payload
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get = AsyncMock(return_value=response)
    return patch("app.services.exchange_rate.httpx.AsyncClient", return_value=client)


@pytest.mark.asyncio
async def test_fetch_uses_the_latest_published_quote():
    payload = {
        "value": [
            {"cotacaoCompra": 5.0113, "cotacaoVenda": 5.0119, "dataHoraCotacao": "2026-10-08 13:08:16.814"},
            {"cotacaoCompra": 4.9886, "cotacaoVenda": 4.9892, "dataHoraCotacao": "2026-10-09 13:07:30.746"},
            {"cotacaoCompra": 4.9692, "cotacaoVenda": 4.9698, "dataHoraCotacao": "2026-10-06 13:03:21.267"},
        ]
    }
    with _ptax_response(payload):
        assert await fetch_usd_brl_ptax() == 4.9892
    # Sem cotação no período (ou resposta inesperada): não inventa valor.
    with _ptax_response({"value": []}):
        assert await fetch_usd_brl_ptax() is None


@pytest.mark.asyncio
async def test_auto_rate_is_refreshed_only_when_stale(db_session, company):
    config = await _config(db_session, company, usd_brl_rate_auto=True)

    with patch("app.services.exchange_rate.fetch_usd_brl_ptax", new_callable=AsyncMock, return_value=4.98923) as fetch:
        await refresh_usd_brl_rate(db_session, config)
        assert config.usd_brl_rate == 4.9892
        assert config.usd_brl_rate_updated_at is not None

        await refresh_usd_brl_rate(db_session, config)  # recém-atualizada: não busca de novo
        assert fetch.await_count == 1

        config.usd_brl_rate_updated_at = datetime.now(timezone.utc) - timedelta(hours=13)
        await refresh_usd_brl_rate(db_session, config)
        assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_manual_rate_is_never_overwritten(db_session, company):
    config = await _config(db_session, company, usd_brl_rate_auto=False)
    config.usd_brl_rate = 5.35

    with patch("app.services.exchange_rate.fetch_usd_brl_ptax", new_callable=AsyncMock, return_value=4.99) as fetch:
        await refresh_usd_brl_rate(db_session, config)

    fetch.assert_not_awaited()
    assert config.usd_brl_rate == 5.35


@pytest.mark.asyncio
async def test_failure_keeps_the_stored_rate_and_does_not_retry_right_away(db_session, company):
    config = await _config(db_session, company, usd_brl_rate_auto=True)

    with patch("app.services.exchange_rate.fetch_usd_brl_ptax", new_callable=AsyncMock, return_value=None) as fetch:
        await refresh_usd_brl_rate(db_session, config)
        await refresh_usd_brl_rate(db_session, config)

    assert config.usd_brl_rate == 5.0
    assert fetch.await_count == 1  # Banco Central fora do ar não atrasa cada abertura do painel
