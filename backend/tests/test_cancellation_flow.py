"""Cancelamento e número geral — não deve disparar envio de planos."""
from app.services.message_flow import (
    _cancellation_flow_active,
    _is_general_unit_inquiry,
    _plan_flow_active,
    _should_block_plan_delivery,
    _student_operational_action,
)


def test_cancellation_detected_on_first_message():
    text = "Olá bom dia Sou Lucilene gostaria se fazer o cancelamento de minha matrícula"
    assert _student_operational_action(text) is True
    assert _cancellation_flow_active(text, []) is True


def test_unit_inquiry_during_cancellation_not_plan_flow():
    history = ["gostaria de fazer o cancelamento de minha matricula"]
    text = "Essa é a unidade de Itaituba ?"
    assert _is_general_unit_inquiry(text) is True
    assert _plan_flow_active(text, history) is False
    assert _should_block_plan_delivery(text, history) is True


def test_cpf_followup_after_cancellation_blocks_plans():
    history = [
        "gostaria de fazer o cancelamento de minha matricula",
        "Essa é a unidade de Itaituba ?",
    ]
    assert _should_block_plan_delivery("01885407254", history) is True


def test_plan_flow_still_works_for_new_lead():
    text = "Quero saber os planos de Santarém"
    assert _plan_flow_active(text, []) is True
    assert _should_block_plan_delivery(text, []) is False
