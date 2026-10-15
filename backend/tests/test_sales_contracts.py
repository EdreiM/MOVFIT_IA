"""Ranking de vendas alimentado pelos contratos que o n8n manda (resumo-pessoa)."""
from datetime import date, datetime
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models import ApiKey, PactoConnection, PactoUnit, SalesContract
from app.security import generate_api_key
from app.services import pacto, sales_contracts, sales_ranking

EMPRESA = "MOVFIT SANTAREM - PA | 0003"
ANUAL = "MOVFIT PREMIUM (ANUAL RECORRENTE)"
AVULSO = "MOVFIT MENSAL AVULSO"


def rec(contrato, responsavel, day="2026-10-02", plano=ANUAL, duracao=12, situacao="Matrícula", **extra):
    """Registro no formato que a Pacto devolve em resumo-pessoa."""
    return {
        "matricula": f"{contrato:06d}",
        "nome": f"CLIENTE {contrato}",
        "lancamento": f"{day}T03:00:00.000+00:00",
        "dataHoraVenda": f"{day}T15:00:00.000+00:00",
        "responsavelVenda": responsavel,
        "consultorResponsavel": ["ELEN C G SOUSA"],
        "plano": plano,
        "empresa": EMPRESA,
        "situacaoContrato": situacao,
        "contrato": contrato,
        "qtd": 1,
        "duracao": duracao,
        "dataInicio": day,
        "produto": situacao.upper(),
        **extra,
    }


async def _ingest(db, company, records, produto="MATRÍCULA", unidade="24h", total=None, de="2026-10-01", ate="2026-10-14"):
    result = await sales_contracts.ingest(
        db, company.id, unidade=unidade, produto=produto, data_inicio=date.fromisoformat(de),
        data_fim=date.fromisoformat(ate), total=len(records) if total is None else total, records=records,
    )
    await db.commit()
    return result


async def _contracts(db, company):
    return (await db.execute(select(SalesContract).where(SalesContract.company_id == company.id))).scalars().all()


def test_lancamento_uses_the_brazilian_day_of_the_utc_midnight_stamp():
    row = sales_contracts.normalize_record(rec(10, "PABLO SILVA BRITO", day="2026-10-01"), "24h", "Matrícula")
    assert row["lancamento"] == date(2026, 10, 1)
    assert row["consultor"] == "ELEN C G SOUSA"
    assert row["duracao"] == 12 and row["empresa_nome"] == EMPRESA
    assert row["vendido_em"] == datetime.fromisoformat("2026-10-01T15:00:00+00:00")


def test_record_without_contract_or_date_is_unusable():
    assert sales_contracts.normalize_record({"nome": "X"}, "24h", "Matrícula") is None
    assert sales_contracts.normalize_record({"contrato": 5}, "24h", "Matrícula") is None


def test_annual_uses_duration_and_falls_back_to_the_plan_name():
    assert sales_contracts.is_annual(12, AVULSO)
    assert not sales_contracts.is_annual(1, ANUAL)
    assert sales_contracts.is_annual(None, ANUAL)
    assert not sales_contracts.is_annual(None, AVULSO)


@pytest.mark.asyncio
async def test_ingest_is_idempotent_and_updates_in_place(db_session, company):
    await _ingest(db_session, company, [rec(1, "PABLO SILVA BRITO"), rec(2, "LUANA CAMILY MOTA GODINHO")])
    again = await _ingest(db_session, company, [rec(1, "PABLO SILVA BRITO"), rec(2, "MARIA GABRIELA SANTOS MOTA")])
    assert again["gravados"] == 2 and again["removidos"] == 0
    rows = {r.contrato: r for r in await _contracts(db_session, company)}
    assert len(rows) == 2
    assert rows[2].responsavel == "MARIA GABRIELA SANTOS MOTA"


@pytest.mark.asyncio
async def test_complete_response_removes_what_disappeared_from_the_period(db_session, company):
    await _ingest(db_session, company, [rec(1, "A B"), rec(2, "A B"), rec(3, "A B", day="2026-09-30")], de="2026-09-01")
    # 2 foi cancelado na Pacto; 3 está fora da janela consultada e não pode ser tocado
    result = await _ingest(db_session, company, [rec(1, "A B")], de="2026-10-01")
    assert result["removidos"] == 1
    assert {r.contrato for r in await _contracts(db_session, company)} == {1, 3}


@pytest.mark.asyncio
async def test_truncated_response_never_deletes_anything(db_session, company):
    await _ingest(db_session, company, [rec(1, "A B"), rec(2, "A B")])
    result = await _ingest(db_session, company, [rec(1, "A B")], total=70)  # veio 1 de 70
    assert result["completo"] is False and result["removidos"] == 0
    assert len(await _contracts(db_session, company)) == 2


@pytest.mark.asyncio
async def test_replace_is_scoped_to_the_product_of_the_request(db_session, company):
    await _ingest(db_session, company, [rec(1, "A B")], produto="MATRÍCULA")
    await _ingest(db_session, company, [rec(2, "A B", situacao="Rematrícula")], produto="REMATRÍCULA")
    # nova consulta de rematrícula vazia não pode apagar a matrícula
    await _ingest(db_session, company, [], produto="REMATRÍCULA")
    assert {r.contrato for r in await _contracts(db_session, company)} == {1}


@pytest.mark.asyncio
async def test_renewal_is_rejected(db_session, company):
    with pytest.raises(ValueError):
        await _ingest(db_session, company, [rec(1, "A B")], produto="RENOVAÇÃO")


@pytest_asyncio.fixture
async def unit(db_session, company):
    u = PactoUnit(company_id=company.id, chave_empresa="chave-24h", empresa_id=1, pacto_name=EMPRESA)
    db_session.add(u)
    await db_session.commit()
    return u


@pytest.mark.asyncio
async def test_ranking_counts_exactly_from_contracts(db_session, company, unit):
    await _ingest(
        db_session, company,
        [
            rec(1, "PABLO SILVA BRITO"),
            rec(2, "PABLO SILVA BRITO"),
            rec(3, "PABLO SILVA BRITO", plano=AVULSO, duracao=1),  # mensal: não conta
            rec(4, "LUANA CAMILY MOTA GODINHO"),
            rec(5, "SITE MOVFITBRASIL"),
            rec(6, "ADMINISTRADOR"),
        ],
    )
    await _ingest(
        db_session, company,
        [
            rec(7, "LUANA CAMILY MOTA GODINHO", situacao="Rematrícula"),
            rec(8, "MARIA GABRIELA SANTOS MOTA", situacao="Rematrícula", plano=AVULSO, duracao=1),
        ],
        produto="REMATRÍCULA",
    )

    data = await sales_ranking.build_ranking(db_session, company.id, "all", date(2026, 10, 1), date(2026, 10, 14))

    assert [(p["nome"], p["novas_anuais"]) for p in data["ranking"]] == [
        ("Luana Camily Mota Godinho", 2),
        ("Pablo Silva Brito", 2),
    ]
    assert data["site"] == 1
    assert data["nao_identificado"] == 1  # ADMINISTRADOR não vira vendedor
    assert data["total_novas_anuais"] == 2 + 2 + 1 + 1
    assert data["matriculas_todas"] == 8
    assert data["fonte"] == "n8n"
    assert data["dias_pendentes"] == 0
    assert data["atualizado_em"] is not None
    assert [u["key"] for u in data["unidades"]] == [f"chave-24h:1"]  # casou com a unidade do MCP


@pytest.mark.asyncio
async def test_period_is_by_launch_day(db_session, company, unit):
    await _ingest(db_session, company, [rec(1, "PABLO SILVA BRITO", day="2026-10-01"), rec(2, "PABLO SILVA BRITO", day="2026-10-13")])
    data = await sales_ranking.build_ranking(db_session, company.id, "all", date(2026, 10, 5), date(2026, 10, 14))
    assert data["ranking"][0]["novas_anuais"] == 1


@pytest.mark.asyncio
async def test_unit_only_known_by_n8n_still_shows_up(db_session, company):
    await _ingest(db_session, company, [rec(1, "PABLO SILVA BRITO")], unidade="itaituba")
    data = await sales_ranking.build_ranking(db_session, company.id, "all", date(2026, 10, 1), date(2026, 10, 14))
    assert len(data["unidades"]) == 1 and data["unidades"][0]["key"].startswith("n8n:")
    assert data["ranking"][0]["nome"] == "Pablo Silva Brito"


@pytest.mark.asyncio
async def test_units_fed_by_n8n_are_skipped_by_the_mcp_collection(db_session, company, unit):
    await _ingest(db_session, company, [rec(1, "PABLO SILVA BRITO")])
    connection = PactoConnection(company_id=company.id, status="connected")
    db_session.add(connection)
    await db_session.commit()

    with patch.object(pacto, "_valid_access_token", AsyncMock(side_effect=AssertionError("não devia usar o MCP"))):
        assert await sales_ranking.sync_company(db_session, connection) is False


@pytest_asyncio.fixture
async def api_key(db_session, company):
    full, prefix, key_hash = generate_api_key()
    db_session.add(
        ApiKey(company_id=company.id, name="n8n vendas", key_prefix=prefix, key_hash=key_hash, can_write_sales=True)
    )
    await db_session.commit()
    return {"Authorization": f"Bearer {full}"}


@pytest.mark.asyncio
async def test_public_api_ingests_and_lists_contracts(client, db_session, company, api_key):
    body = {
        "unidade": "24h", "produto": "MATRÍCULA", "data_inicio": "2026-10-01", "data_fim": "2026-10-14",
        "total": 2, "contratos": [rec(1, "PABLO SILVA BRITO"), rec(2, "LUANA CAMILY MOTA GODINHO", plano=AVULSO, duracao=1)],
    }
    assert (await client.post("/v1/sales/contracts", json=body)).status_code == 401

    resp = await client.post("/v1/sales/contracts", json=body, headers=api_key)
    assert resp.status_code == 200, resp.text
    assert resp.json()["sucesso"] is True and resp.json()["dados"]["gravados"] == 2

    listed = await client.get("/v1/sales/contracts?date_from=2026-10-01&responsavel=luana", headers=api_key)
    contratos = listed.json()["dados"]["contratos"]
    assert len(contratos) == 1 and contratos[0]["anual"] is False


@pytest.mark.asyncio
async def test_public_api_rejects_renewal_and_bad_period(client, db_session, company, api_key):
    base = {"unidade": "24h", "data_inicio": "2026-10-01", "data_fim": "2026-10-14", "contratos": []}
    renewal = await client.post("/v1/sales/contracts", json={**base, "produto": "RENOVAÇÃO"}, headers=api_key)
    assert renewal.status_code == 400
    inverted = await client.post(
        "/v1/sales/contracts",
        json={**base, "produto": "MATRÍCULA", "data_inicio": "2026-10-14", "data_fim": "2026-10-01"},
        headers=api_key,
    )
    assert inverted.status_code == 400


@pytest.mark.asyncio
async def test_seller_contracts_lists_counted_and_not_counted(client, db_session, company, user, unit):
    await _ingest(
        db_session, company,
        [rec(1, "LUANA CAMILY MOTA GODINHO", day="2026-10-05"), rec(2, "LUANA CAMILY MOTA GODINHO", plano=AVULSO, duracao=1)],
    )
    login = await client.post("/auth/login", json={"email": "teste@movfit.com", "password": "senha-teste-123"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    resp = await client.get(
        "/sales/seller-contracts?name=Luana Camily Mota Godinho&date_from=2026-10-01&date_to=2026-10-14", headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["disponivel"] is True and body["total"] == 1
    by_contract = {c["matricula"]: c for c in body["contratos"]}
    assert by_contract["000001"]["conta"] is True and by_contract["000001"]["motivo"] is None
    assert by_contract["000002"]["conta"] is False and "não anual" in by_contract["000002"]["motivo"]

    ranking = (await client.get("/sales/ranking?date_from=2026-10-01&date_to=2026-10-14", headers=headers)).json()
    assert ranking["fonte"] == "n8n" and ranking["conexao"] == "connected"


@pytest.mark.asyncio
async def test_read_only_key_can_list_but_never_write_sales(client, db_session, company):
    full, prefix, key_hash = generate_api_key()
    db_session.add(ApiKey(company_id=company.id, name="link de métricas", key_prefix=prefix, key_hash=key_hash))
    await db_session.commit()
    headers = {"Authorization": f"Bearer {full}"}
    body = {
        "unidade": "24h", "produto": "MATRÍCULA", "data_inicio": "2026-10-01", "data_fim": "2026-10-14",
        "total": 1, "contratos": [rec(1, "PABLO SILVA BRITO")],
    }

    denied = await client.post("/v1/sales/contracts", json=body, headers=headers)
    assert denied.status_code == 403
    assert await _contracts(db_session, company) == []
    assert (await client.get("/v1/sales/contracts", headers=headers)).status_code == 200


@pytest.mark.asyncio
async def test_panel_creates_a_key_that_can_write_sales(client, db_session, company, user):
    login = await client.post("/auth/login", json={"email": "teste@movfit.com", "password": "senha-teste-123"})
    admin = {"Authorization": f"Bearer {login.json()['access_token']}"}

    plain = await client.post("/api-keys", json={"name": "só leitura"}, headers=admin)
    assert plain.status_code == 200 and plain.json()["can_write_sales"] is False
    writer = await client.post("/api-keys", json={"name": "n8n vendas", "can_write_sales": True}, headers=admin)
    assert writer.json()["can_write_sales"] is True

    body = {
        "unidade": "24h", "produto": "MATRÍCULA", "data_inicio": "2026-10-01", "data_fim": "2026-10-14",
        "total": 1, "contratos": [rec(1, "PABLO SILVA BRITO")],
    }
    ok = await client.post("/v1/sales/contracts", json=body, headers={"Authorization": f"Bearer {writer.json()['key']}"})
    assert ok.status_code == 200, ok.text
    no = await client.post("/v1/sales/contracts", json=body, headers={"Authorization": f"Bearer {plain.json()['key']}"})
    assert no.status_code == 403
