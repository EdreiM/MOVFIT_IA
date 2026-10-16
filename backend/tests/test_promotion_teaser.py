"""Promoção no modo vendedor: a IA só PERGUNTA se o cliente quer saber; a explicação,
o banner e a transferência vêm depois."""
from datetime import datetime, timedelta, timezone

from app.models import Message, Plan, Promotion
from app.services.message_flow import SalesMediaContext, _pick_sales_media_for_bubble
from app.services.promotions import is_promotion_teaser, sales_promotion_to_transfer

TITLE = "Outubro Rosa — 50% na 1ª mensalidade"
TEASER = "Tenho também uma condição especial de Outubro Rosa — quer que eu te conte?"
DETAILS = (
    "No Outubro Rosa, novas alunas pagam só metade da primeira mensalidade e ganham um dia de treino "
    "grátis pra conhecer a academia."
)
CLOSING = "Quer aproveitar essa condição? Te passo pra um atendente garantir."


def _msg(actor, text, minutes_ago):
    return Message(
        actor=actor,
        direction="inbound" if actor == "customer" else "outbound",
        content_type="text",
        text=text,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )


def _promotion(**overrides):
    base = dict(title=TITLE, message=DETAILS, requires_transfer=True, trigger_keywords=[], image_url="https://x.test/rosa.png")
    base.update(overrides)
    return Promotion(**base)


def test_only_a_short_question_offering_to_tell_is_a_teaser():
    assert is_promotion_teaser(TEASER)
    assert is_promotion_teaser("Quer saber de uma promoção de outubro pra mulheres?")
    # explica de verdade, ou não pergunta, ou é outra pergunta: não é isca
    assert not is_promotion_teaser(DETAILS)
    assert not is_promotion_teaser("Tenho uma condição especial de Outubro Rosa.")
    assert not is_promotion_teaser(CLOSING)
    assert not is_promotion_teaser("Posso te mandar o link pra garantir a vaga?")
    assert not is_promotion_teaser(DETAILS + " Quer saber mais sobre os planos? " + "x" * 200)
    assert not is_promotion_teaser(None)


def test_yes_to_the_teaser_is_not_acceptance_of_the_promotion():
    promotion = _promotion()
    history = [
        _msg("customer", "queria ver os planos", 5),
        _msg("ai", "O anual sai por R$ 197,00 por mês.", 4),
        _msg("ai", TEASER, 4),
        _msg("customer", "sim", 0),
    ]
    assert sales_promotion_to_transfer("sim", history, [promotion]) is None
    assert sales_promotion_to_transfer("pode contar", history, [promotion]) is None


def test_after_the_explanation_the_customers_yes_does_transfer():
    promotion = _promotion()
    history = [
        _msg("customer", "queria ver os planos", 9),
        _msg("ai", TEASER, 8),
        _msg("customer", "sim", 7),
        _msg("ai", DETAILS, 6),
        _msg("ai", CLOSING, 6),
        _msg("customer", "sim", 0),
    ]
    assert sales_promotion_to_transfer("sim", history, [promotion]) is promotion


def test_customer_asking_for_the_promotion_in_his_own_words_still_transfers_after_a_teaser():
    promotion = _promotion()
    history = [_msg("ai", TEASER, 4), _msg("customer", "quero o desconto", 0)]
    assert sales_promotion_to_transfer("quero o desconto", history, [promotion]) is promotion


def test_the_banner_goes_with_the_explanation_not_with_the_teaser():
    promotion = _promotion()
    plan = Plan(name="Plano Mensal Recorrente", monthly_price=217.0, image_url=None)
    context = SalesMediaContext(unit_id=None, unit_name=None, plans=[plan], promotions=[promotion])

    assert _pick_sales_media_for_bubble(TEASER, context, set()) == []
    assert _pick_sales_media_for_bubble(DETAILS, context, set()) == [promotion]
