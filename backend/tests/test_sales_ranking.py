"""Ranking de vendas: novas matrículas anuais por responsável."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models import PactoConnection, PactoUnit, SalesDaily, SalesSyncDay, User, UserCompany
from app.security import hash_password
from app.services import pacto, sales_ranking
from app.services.sales_ranking import _Budget, _Row, allocate_non_annual, is_annual_plan, parse_report

ANUAL = "MOVFIT PREMIUM (ANUAL RECORRENTE) - 12 - cod. 33"
PARCELADO = "MOVFIT PREMIUM (ANUAL PARCELADO) - 12 - cod. 34"
AVULSO = "MOVFIT MENSAL AVULSO - 1 - cod. 35"
DAY = date(2026, 10, 2)


class FakePacto:
    """Servidor de relatórios simulado: cada venda é um dict com dia, responsável,
    tipo (Matrícula/Rematrícula/Renovação) e plano."""

    def __init__(self, sales, responsaveis=None):
        self.sales = sales
        self.responsaveis = responsaveis or {}
        self.calls: list[tuple[str, dict]] = []

    def _select(self, args):
        start = date.fromisoformat(args["data_inicio"])
        end = date.fromisoformat(args.get("data_termino") or args["data_inicio"])
        resp = args.get("responsavel_id")
        return [
            s for s in self.sales
            if start <= s["day"] <= end and (resp is None or s["responsavel_id"] == resp)
        ]

    async def call_tool(self, name, args=None):
        args = args or {}
        self.calls.append((name, args))
        if name == "relatorio_faturamento_empresa":
            sales = self._select(args)
            kinds, plans = {}, {}
            for s in sales:
                kinds[s["tipo"].upper()] = kinds.get(s["tipo"].upper(), 0) + 1
                plans[s["plano"]] = plans.get(s["plano"], 0) + 1
            groups = []
            if kinds:
                groups.append({
                    "tipo_produto": "Matrícula, Rematrícula, Renovação",
                    "produtos": [{"descricao": k, "quantidade": q} for k, q in kinds.items()],
                })
                groups.append({
                    "tipo_produto": "Mês de Referência Plano",
                    "produtos": [{"descricao": p, "quantidade": q} for p, q in plans.items()],
                })
            return {"por_tipo_produto": groups}
        if name == "vendas_produto_faturamento_empresa":
            sales = [s for s in self._select(args) if s["plano"] == args["produto"]]
            return {"vendas": [{"nome": f"Cliente {i}", "tipo_venda": s["tipo"]} for i, s in enumerate(sales)]}
        if name == "listar_responsaveis_lancamento_empresa":
            return {"responsaveis": [{"responsavel_id": i, "nome": n} for i, n in self.responsaveis.items()]}
        raise AssertionError(f"ferramenta inesperada: {name}")


def sale(rid, tipo, plano, day=DAY):
    return {"day": day, "responsavel_id": rid, "tipo": tipo, "plano": plano}


@pytest_asyncio.fixture
async def unit(db_session, company):
    u = PactoUnit(company_id=company.id, chave_empresa="chave-24h", empresa_id=1, pacto_name="MOVFIT SANTAREM - PA | 0003")
    db_session.add(u)
    await db_session.commit()
    return u


async def _sync_day(db, company, unit, fake, day=DAY, sellers=None):
    await sales_ranking.sync_unit_day(
        db, fake, _Budget(1000), company.id, unit, day, sellers if sellers is not None else [], {}
    )
    await db.commit()


async def _rows(db, company):
    result = await db.execute(select(SalesDaily).where(SalesDaily.company_id == company.id))
    return {r.responsavel_nome: r for r in result.scalars().all()}


def test_is_annual_plan_uses_duration_in_the_name():
    assert is_annual_plan(ANUAL)
    assert is_annual_plan(PARCELADO)
    assert not is_annual_plan(AVULSO)
    assert not is_annual_plan("PLANO SEMESTRAL - 6 - cod. 40")
    assert is_annual_plan("PLANO ANUAL SEM CODIGO")


def test_display_name_keeps_particles_lowercase():
    assert sales_ranking.display_name("CELLINE VITORIA DA SILVA XAVIER") == "Celline Vitoria da Silva Xavier"
    assert sales_ranking.display_name("DA SILVA") == "Da Silva"


def test_parse_report_separates_sale_types_and_plans():
    report = {
        "por_tipo_produto": [
            {"tipo_produto": "Matrícula, Rematrícula, Renovação", "produtos": [
                {"descricao": "MATRÍCULA", "quantidade": 63},
                {"descricao": "REMATRÍCULA", "quantidade": 5},
                {"descricao": "RENOVAÇÃO", "quantidade": 470},
            ]},
            {"tipo_produto": "Mês de Referência Plano", "produtos": [
                {"descricao": ANUAL, "quantidade": 505},
                {"descricao": AVULSO, "quantidade": 3},
            ]},
        ]
    }
    counts = parse_report(report)
    assert (counts.matriculas, counts.rematriculas, counts.renovacoes) == (63, 5, 470)
    assert counts.non_annual_total == 3


@pytest.mark.asyncio
async def test_only_new_annual_matriculas_count(db_session, company, unit):
    fake = FakePacto(
        [
            sale(59, "Matrícula", AVULSO),   # Alohanna: matrícula avulsa não conta
            sale(59, "Matrícula", ANUAL),
            sale(135, "Matrícula", ANUAL),   # Pablo: 2 matrículas anuais + 1 renovação
            sale(135, "Matrícula", PARCELADO),
            sale(135, "Renovação", ANUAL),
        ],
        responsaveis={59: "ALOHANNA RIBEIRO SANTOS", 135: "PABLO SILVA BRITO"},
    )
    await _sync_day(db_session, company, unit, fake)

    rows = await _rows(db_session, company)
    assert rows["ALOHANNA RIBEIRO SANTOS"].matriculas == 2
    assert rows["ALOHANNA RIBEIRO SANTOS"].novas_anuais == 1
    assert rows["PABLO SILVA BRITO"].novas_anuais == 2
    assert rows["PABLO SILVA BRITO"].renovacoes == 1
    assert not rows["ALOHANNA RIBEIRO SANTOS"].estimated


@pytest.mark.asyncio
async def test_renewal_in_a_non_annual_plan_is_not_a_new_matricula(db_session, company, unit):
    fake = FakePacto(
        [sale(278, "Matrícula", ANUAL), sale(278, "Rematrícula", AVULSO)],
        responsaveis={278: "LUANA CAMILY MOTA GODINHO"},
    )
    await _sync_day(db_session, company, unit, fake)

    row = (await _rows(db_session, company))["LUANA CAMILY MOTA GODINHO"]
    # o avulso era a REMATRÍCULA (a lista venda a venda diz isso): a matrícula é anual
    assert row.matriculas == 1 and row.novas_anuais == 1
    assert row.rematriculas == 1
    assert row.estimated is True  # separação feita pela lista da unidade, não só pelo responsável


@pytest.mark.asyncio
async def test_non_annual_matricula_mixed_with_rematricula_is_subtracted(db_session, company, unit):
    fake = FakePacto(
        [sale(7, "Matrícula", AVULSO), sale(7, "Rematrícula", ANUAL)],
        responsaveis={7: "FULANO"},
    )
    await _sync_day(db_session, company, unit, fake)

    row = (await _rows(db_session, company))["FULANO"]
    assert row.matriculas == 1 and row.novas_anuais == 0


@pytest.mark.asyncio
async def test_sales_nobody_claimed_become_unassigned(db_session, company, unit):
    fake = FakePacto([sale(999, "Matrícula", ANUAL)], responsaveis={1: "ALGUEM"})
    await _sync_day(db_session, company, unit, fake)

    row = (await _rows(db_session, company))[sales_ranking.UNASSIGNED_NAME]
    assert row.responsavel_id == 0 and row.matriculas == 1


@pytest.mark.asyncio
async def test_day_without_sales_is_marked_synced_and_unchanged_day_is_cheap(db_session, company, unit):
    empty_day = DAY + timedelta(days=1)
    fake = FakePacto([sale(59, "Matrícula", ANUAL)], responsaveis={59: "ALOHANNA"})

    await _sync_day(db_session, company, unit, fake, day=empty_day)
    marker = await db_session.scalar(select(SalesSyncDay).where(SalesSyncDay.day == empty_day))
    assert marker is not None and marker.matriculas_total == 0
    assert await _rows(db_session, company) == {}

    await _sync_day(db_session, company, unit, fake)
    calls_after_first = len(fake.calls)
    await _sync_day(db_session, company, unit, fake)  # nada mudou
    assert len(fake.calls) - calls_after_first == 1  # só a conferência do total da unidade


def test_allocation_never_exceeds_the_unit_budget():
    a = _Row(1, "A", parse_report({"por_tipo_produto": [
        {"tipo_produto": "Matrícula, Rematrícula, Renovação", "produtos": [
            {"descricao": "MATRÍCULA", "quantidade": 1}, {"descricao": "RENOVAÇÃO", "quantidade": 1}]},
        {"tipo_produto": "Mês de Referência Plano", "produtos": [{"descricao": AVULSO, "quantidade": 1}]},
    ]}))
    b = _Row(2, "B", parse_report({"por_tipo_produto": [
        {"tipo_produto": "Matrícula, Rematrícula, Renovação", "produtos": [
            {"descricao": "MATRÍCULA", "quantidade": 1}, {"descricao": "REMATRÍCULA", "quantidade": 1}]},
        {"tipo_produto": "Mês de Referência Plano", "produtos": [{"descricao": AVULSO, "quantidade": 1}]},
    ]}))
    allocate_non_annual([a, b], na_unit_matriculas=1)  # só 1 das 2 avulsas foi matrícula
    assert a.na + b.na == 1
    assert a.estimated and b.estimated


# ----------------------------------------------------------- agregação


async def _seed_days(db, company, unit, other_unit):
    def add(u, day, rid, nome, novas, matriculas=None):
        db.add(
            SalesDaily(
                company_id=company.id, chave_empresa=u.chave_empresa, empresa_id=u.empresa_id, day=day,
                responsavel_id=rid, responsavel_nome=nome, matriculas=matriculas if matriculas is not None else novas,
                novas_anuais=novas,
            )
        )

    add(unit, date(2026, 10, 1), 135, "PABLO SILVA BRITO", 5)
    add(unit, date(2026, 10, 2), 135, "PABLO SILVA BRITO", 7)
    add(unit, date(2026, 10, 2), 59, "ALOHANNA RIBEIRO SANTOS", 11, matriculas=12)
    add(unit, date(2026, 10, 2), 342, "SITE MOVFITBRASIL", 2)
    add(other_unit, date(2026, 10, 3), 135, "PABLO SILVA BRITO", 1)  # mesmo nome, outra unidade
    add(unit, date(2026, 10, 20), 59, "ALOHANNA RIBEIRO SANTOS", 4)  # fora do período
    await db.commit()


@pytest.mark.asyncio
async def test_ranking_merges_units_separates_site_and_respects_period(db_session, company, unit):
    other = PactoUnit(company_id=company.id, chave_empresa="chave-itb", empresa_id=1, pacto_name="MOVFIT ITAITUBA")
    db_session.add(other)
    await db_session.commit()
    await _seed_days(db_session, company, unit, other)

    data = await sales_ranking.build_ranking(db_session, company.id, "all", date(2026, 10, 1), date(2026, 10, 13))

    assert [(p["nome"], p["novas_anuais"]) for p in data["ranking"]] == [
        ("Pablo Silva Brito", 13),
        ("Alohanna Ribeiro Santos", 11),
    ]
    assert data["ranking"][0]["posicao"] == 1
    assert data["equipe"] == 24 and data["site"] == 2 and data["total_novas_anuais"] == 26
    assert data["matriculas_todas"] == 27  # 5 + 7 + 12 + 2 + 1


@pytest.mark.asyncio
async def test_ranking_filters_by_unit(db_session, company, unit):
    other = PactoUnit(company_id=company.id, chave_empresa="chave-itb", empresa_id=1, pacto_name="MOVFIT ITAITUBA")
    db_session.add(other)
    await db_session.commit()
    await _seed_days(db_session, company, unit, other)

    only_itb = await sales_ranking.build_ranking(
        db_session, company.id, "chave-itb:1", date(2026, 10, 1), date(2026, 10, 13)
    )
    assert [(p["nome"], p["novas_anuais"]) for p in only_itb["ranking"]] == [("Pablo Silva Brito", 1)]
    assert only_itb["unidade"] == "chave-itb:1"


@pytest.mark.asyncio
async def test_ranking_reports_days_still_loading(db_session, company, unit):
    today = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    db_session.add(SalesSyncDay(company_id=company.id, chave_empresa=unit.chave_empresa, empresa_id=1, day=today))
    await db_session.commit()

    data = await sales_ranking.build_ranking(db_session, company.id, "all", today - timedelta(days=2), today)
    assert data["dias_pendentes"] == 2  # 3 dias esperados, 1 já coletado
    assert data["atualizado_em"] is not None


# ------------------------------------------------------ coleta completa


@pytest.mark.asyncio
async def test_sync_company_backfills_recent_days_and_reuses_the_token(db_session, company, unit, monkeypatch):
    today = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    fake = FakePacto(
        [sale(135, "Matrícula", ANUAL, day=today), sale(135, "Matrícula", ANUAL, day=today - timedelta(days=1))],
        responsaveis={135: "PABLO SILVA BRITO"},
    )

    class _Session:
        def __init__(self, *_a, **_k):
            pass

        async def __aenter__(self):
            return fake

        async def __aexit__(self, *_):
            return None

    monkeypatch.setattr(sales_ranking.get_settings(), "sales_backfill_days", 5)
    connection = PactoConnection(company_id=company.id, status="connected")
    db_session.add(connection)
    await db_session.commit()

    with patch.object(pacto, "_valid_access_token", AsyncMock(return_value="tok")), patch.object(
        pacto, "PactoSession", _Session
    ):
        pending = await sales_ranking.sync_company(db_session, connection)

    assert pending is False
    synced = (await db_session.execute(select(SalesSyncDay.day))).scalars().all()
    assert len(synced) == 5
    data = await sales_ranking.build_ranking(db_session, company.id, "all", today - timedelta(days=4), today)
    assert data["ranking"][0]["nome"] == "Pablo Silva Brito" and data["ranking"][0]["novas_anuais"] == 2
    assert data["dias_pendentes"] == 0


@pytest.mark.asyncio
async def test_sync_company_stops_at_the_call_budget_and_asks_to_continue(db_session, company, unit, monkeypatch):
    fake = FakePacto([], responsaveis={})

    class _Session:
        def __init__(self, *_a, **_k):
            pass

        async def __aenter__(self):
            return fake

        async def __aexit__(self, *_):
            return None

    monkeypatch.setattr(sales_ranking.get_settings(), "sales_backfill_days", 10)
    connection = PactoConnection(company_id=company.id, status="connected")
    db_session.add(connection)
    await db_session.commit()

    with patch.object(pacto, "_valid_access_token", AsyncMock(return_value="tok")), patch.object(
        pacto, "PactoSession", _Session
    ):
        pending = await sales_ranking.sync_company(db_session, connection, max_calls=3)

    assert pending is True
    assert len(fake.calls) == 3


# ---------------------------------------------------------- permissões


async def _login(client, email, password):
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest_asyncio.fixture
async def sales_user(db_session, company):
    u = User(
        email="supervisora@movfit.com", hashed_password=hash_password("senha-vendas-1"),
        full_name="Supervisora", role="vendas", status="active",
    )
    db_session.add(u)
    await db_session.flush()
    db_session.add(UserCompany(user_id=u.id, company_id=company.id))
    await db_session.commit()
    return u


@pytest.mark.asyncio
async def test_sales_role_reaches_only_the_ranking(client, db_session, company, unit, sales_user):
    db_session.add(
        SalesDaily(
            company_id=company.id, chave_empresa=unit.chave_empresa, empresa_id=1, day=date(2026, 10, 2),
            responsavel_id=135, responsavel_nome="PABLO SILVA BRITO", matriculas=3, novas_anuais=3,
        )
    )
    await db_session.commit()
    headers = await _login(client, "supervisora@movfit.com", "senha-vendas-1")

    ok = await client.get("/sales/ranking?date_from=2026-10-01&date_to=2026-10-13", headers=headers)
    assert ok.status_code == 200
    assert ok.json()["ranking"][0]["nome"] == "Pablo Silva Brito"
    assert (await client.get("/auth/me", headers=headers)).status_code == 200

    for path in ("/conversations", "/pacto", "/leads", "/admin/events", "/ai-config", "/metrics/overview"):
        resp = await client.get(path, headers=headers)
        assert resp.status_code in (403, 404, 405), f"{path} devolveu {resp.status_code}"
        assert resp.status_code != 200


@pytest.mark.asyncio
async def test_sales_ranking_rejects_inverted_period(client, db_session, company, sales_user):
    headers = await _login(client, "supervisora@movfit.com", "senha-vendas-1")
    resp = await client.get("/sales/ranking?date_from=2026-10-13&date_to=2026-10-01", headers=headers)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_admin_can_use_the_ranking_too(client, db_session, company, user):
    headers = await _login(client, "teste@movfit.com", "senha-teste-123")
    resp = await client.get("/sales/ranking", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["ranking"] == []
