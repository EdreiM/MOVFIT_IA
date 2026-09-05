import pytest
import pytest_asyncio

from app.services.message_flow import (
    _best_matches,
    _get_sent_plan_image_urls,
    _normalize_tokens,
    _resolve_plan_images,
    _unique_match,
)


def test_normalize_tokens_ignores_accents_and_case():
    assert _normalize_tokens("Santarém - 24 Horas") == {"santarem", "24", "horas"}


def test_best_matches_picks_highest_overlap():
    candidates = [
        ("unidade-1", {"santarem", "24", "horas", "premium"}),
        ("unidade-2", {"santarem", "nova", "republica"}),
    ]
    # A IA parafraseou, mas ainda tem sobreposição suficiente com a unidade 1.
    arg_tokens = _normalize_tokens("MOVFIT Santarém — Premium (24h)")
    assert _best_matches(candidates, arg_tokens, threshold=0.4) == ["unidade-1"]


def test_best_matches_returns_all_tied_candidates():
    candidates = [
        ("unidade-1", {"santarem", "a"}),
        ("unidade-2", {"santarem", "b"}),
    ]
    arg_tokens = {"santarem"}
    # Ambas batem 50% — _best_matches devolve as duas, sem decidir.
    assert set(_best_matches(candidates, arg_tokens, threshold=0.4)) == {"unidade-1", "unidade-2"}


def test_unique_match_treats_tie_as_no_match():
    candidates = [
        ("unidade-1", {"santarem", "a"}),
        ("unidade-2", {"santarem", "b"}),
    ]
    arg_tokens = {"santarem"}
    # Quem decide o que fazer com o empate é _unique_match — aqui sim, empate vira None.
    assert _unique_match(candidates, arg_tokens, threshold=0.4) is None


def test_unique_match_returns_sole_winner():
    candidates = [
        ("unidade-1", {"santarem", "24", "horas", "premium"}),
        ("unidade-2", {"santarem", "nova", "republica"}),
    ]
    arg_tokens = _normalize_tokens("MOVFIT Santarém — Premium (24h)")
    assert _unique_match(candidates, arg_tokens, threshold=0.4) == "unidade-1"


@pytest_asyncio.fixture
async def unit_with_plans(db_session, company):
    from app.models import Plan, Unit

    unit = Unit(company_id=company.id, name="Santarém - 24 horas", city="Santarém", unit_type="Premium")
    db_session.add(unit)
    await db_session.flush()

    plan_anual = Plan(
        company_id=company.id,
        unit_id=unit.id,
        name="Plano Anual Parcelado",
        monthly_price=197.0,
        image_url="https://exemplo.com/anual.png",
    )
    plan_mensal = Plan(
        company_id=company.id,
        unit_id=unit.id,
        name="Plano Mensal Recorrente",
        monthly_price=217.0,
        image_url="https://exemplo.com/mensal.png",
    )
    db_session.add_all([plan_anual, plan_mensal])
    await db_session.commit()
    return unit, plan_anual, plan_mensal


@pytest.mark.asyncio
async def test_resolve_plan_images_matches_paraphrased_unit_name(db_session, company, unit_with_plans):
    unit, plan_anual, plan_mensal = unit_with_plans

    images = await _resolve_plan_images(
        db_session, company.id, {"Unidade": "MOVFIT Santarém — Premium (24h)"}
    )

    assert {img["url"] for img in images} == {plan_anual.image_url, plan_mensal.image_url}


@pytest.mark.asyncio
async def test_resolve_plan_images_filters_by_plan_name(db_session, company, unit_with_plans):
    unit, plan_anual, plan_mensal = unit_with_plans

    images = await _resolve_plan_images(
        db_session, company.id, {"Unidade": "Santarém - 24 horas", "Nome do Plano": "Plano Mensal Recorrente"}
    )

    assert [img["url"] for img in images] == [plan_mensal.image_url]


@pytest.mark.asyncio
async def test_resolve_plan_images_no_match_returns_empty(db_session, company, unit_with_plans):
    images = await _resolve_plan_images(db_session, company.id, {"Unidade": "Unidade que não existe"})
    assert images == []


@pytest.mark.asyncio
async def test_get_sent_plan_image_urls_reads_from_history(db_session, company):
    from app.models import Conversation, Message

    conv = Conversation(company_id=company.id, contact_phone="5511999997777", channel="webhook")
    db_session.add(conv)
    await db_session.flush()

    msg = Message(
        conversation_id=conv.id,
        company_id=company.id,
        direction="outbound",
        actor="ai",
        content_type="image",
        raw_payload={"images": [{"unidade": "X", "plano": "Y", "url": "https://exemplo.com/ja-enviada.png"}]},
    )
    db_session.add(msg)
    await db_session.commit()

    sent = await _get_sent_plan_image_urls(db_session, conv.id)
    assert sent == {"https://exemplo.com/ja-enviada.png"}
