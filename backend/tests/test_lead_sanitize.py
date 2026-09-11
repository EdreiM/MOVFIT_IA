"""CPF e campos de Lead não podem gravar template n8n nem derrubar o flush."""
from app.services.message_flow import (
    _CUSTOMER_TRANSFER_ON_TOOL_FAILURE,
    _extract_cpf_from_text,
    _format_tool_result_as_reply,
    _is_technical_tool_failure,
    _looks_like_unresolved_template,
    _sanitize_cpf,
    _sanitize_lead_fields,
)


def test_rejects_n8n_template_cpf():
    bad = "={{ $('Normalizar').item.json.cpf }}"
    assert _looks_like_unresolved_template(bad) is True
    assert _sanitize_cpf(bad) is None
    assert _sanitize_lead_fields({"cpf": bad}) == {}


def test_accepts_real_cpf_formats():
    assert _sanitize_cpf("030.622.992-74") == "03062299274"
    assert _extract_cpf_from_text("030.622.992-74 tente novamente") == "03062299274"
    assert _sanitize_lead_fields({"cpf": "03062299274"}) == {"cpf": "03062299274"}


def test_rejects_cpf_wrong_length():
    assert _sanitize_cpf("123456") is None


def test_technical_tool_failure_never_reaches_customer():
    result = {"sucesso": False, "mensagem": "Falha ao executar a ferramenta agora."}
    assert _is_technical_tool_failure(result) is True
    assert _format_tool_result_as_reply(result) is None


def test_business_tool_failure_can_reach_customer():
    result = {"sucesso": False, "mensagem": "Não encontramos matrícula ativa com esse CPF."}
    assert _is_technical_tool_failure(result) is False
    assert "matrícula" in (_format_tool_result_as_reply(result) or "")


def test_transfer_message_has_no_technical_wording():
    assert "falha" not in _CUSTOMER_TRANSFER_ON_TOOL_FAILURE.lower()
    assert "ferramenta" not in _CUSTOMER_TRANSFER_ON_TOOL_FAILURE.lower()
