"""Links personalizados da IA — matching e reforço determinístico."""
from app.services.message_flow import (
    _custom_link_matches_text,
    _ensure_custom_links_in_reply,
    _format_custom_links_system_block,
    _matching_custom_links,
)

CAREERS_LINK = {
    "label": "Trabalhe conosco",
    "url": "https://movfit.rhgestor.com.br/vagas/?cidade=santarem-pa-1506807&cidadeCargo=&nomeVaga=",
    "when": "vagas, emprego, trabalhar, trabalho, colaborador, personal, recepcionista, currículo, seleção",
}


def test_custom_link_matches_careers_intent():
    assert _custom_link_matches_text(CAREERS_LINK, "Como faço para trabalhar com vocês?") is True
    assert _custom_link_matches_text(CAREERS_LINK, "Quero me inscrever como personal") is True
    assert _custom_link_matches_text(CAREERS_LINK, "Qual o horário da academia?") is False


def test_matching_custom_links_uses_recent_context():
    matched = _matching_custom_links(
        "sim, quero",
        ["Tem vagas de recepcionista?"],
        [CAREERS_LINK],
    )
    assert len(matched) == 1
    assert matched[0]["url"] == CAREERS_LINK["url"]


def test_format_custom_links_system_block():
    block = _format_custom_links_system_block([CAREERS_LINK])
    assert "Trabalhe conosco" in block
    assert CAREERS_LINK["url"] in block
    assert "vagas" in block


def test_ensure_custom_links_appends_missing_url():
    reply = _ensure_custom_links_in_reply(
        "Claro! Você pode se candidatar pelas vagas abertas.",
        "Como trabalho aí?",
        [],
        [CAREERS_LINK],
    )
    assert CAREERS_LINK["url"] in reply


def test_ensure_custom_links_skips_when_already_present():
    reply = _ensure_custom_links_in_reply(
        f"Veja as vagas aqui: {CAREERS_LINK['url']}",
        "Quero trabalhar com vocês",
        [],
        [CAREERS_LINK],
    )
    assert reply.count(CAREERS_LINK["url"]) == 1
