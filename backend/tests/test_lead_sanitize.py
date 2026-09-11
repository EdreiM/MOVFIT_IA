"""CPF e campos de Lead não podem gravar template n8n nem derrubar o flush."""
from app.services.message_flow import (
    _extract_cpf_from_text,
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
