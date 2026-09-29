"""Bloqueio de pitch proibido (ex: convite de grupo VIP)."""
from app.services.message_flow import (
    _filter_forbidden_proactive_reply,
    _is_forbidden_proactive_pitch,
)


def test_detects_vip_group_pitch():
    text = (
        "Olá, tudo bem? Vi que você demonstrou interesse na MOVFIT Express – Nova República. "
        "Caso você não tenha conseguido entrar no nosso grupo VIP, é só clicar no link."
    )
    assert _is_forbidden_proactive_pitch(text) is True


def test_keeps_normal_plan_closing():
    text = (
        "Enviei para você os planos disponíveis na unidade Santarém - Nova República. "
        "Se quiser, posso ajudar a agendar um tour para você conhecer a academia."
    )
    assert _is_forbidden_proactive_pitch(text) is False


def test_filter_removes_only_forbidden_paragraph():
    good = "Enviei os planos. Posso ajudar com mais alguma coisa?"
    bad = "Vi que você demonstrou interesse. Entre no nosso grupo VIP pelo link."
    filtered = _filter_forbidden_proactive_reply(f"{good}\n\n{bad}")
    assert "grupo VIP" not in filtered
    assert "Enviei os planos" in filtered
