"""Uma ferramenta com tool_key inválida pro OpenAI não pode derrubar a IA.

Regressão de produção: chave com acento/espaço era aceita no cadastro e fazia
a API do OpenAI recusar TODA requisição (400), então nenhuma conversa recebia
resposta — nem um simples "oi".
"""
import pytest
from pydantic import ValidationError

from app.models import Tool
from app.schemas import ToolCreate
from app.services.message_flow import _is_valid_openai_tool, _tool_to_openai_schema


def _tool(tool_key: str, parameters: list | None = None) -> Tool:
    return Tool(
        name="Ferramenta",
        tool_key=tool_key,
        description="desc",
        parameters=parameters or [],
        webhook_url="https://example.com/webhook",
    )


@pytest.mark.parametrize(
    "tool_key",
    ["verificar_unidade_por_cpf", "consultar-pagamento", "tool123", "A_b-9"],
)
def test_valid_tool_keys_are_accepted(tool_key):
    assert _is_valid_openai_tool(_tool(tool_key)) is True


@pytest.mark.parametrize(
    "tool_key",
    [
        "verificar_convidados_mês",  # acento
        "verificar convidados",  # espaço
        "consultar.pagamento",  # ponto
        "",  # vazia
        "a" * 65,  # longa demais
    ],
)
def test_invalid_tool_keys_are_rejected(tool_key):
    assert _is_valid_openai_tool(_tool(tool_key)) is False


def test_parameters_without_name_are_skipped():
    schema = _tool_to_openai_schema(
        _tool(
            "consultar_aluno",
            [
                {"name": "cpf", "type": "string", "required": True},
                {"name": "   ", "type": "string", "required": True},
                {"type": "string"},
            ],
        )
    )
    assert list(schema["function"]["parameters"]["properties"]) == ["cpf"]
    assert schema["function"]["parameters"]["required"] == ["cpf"]


def test_tool_create_rejects_invalid_key():
    with pytest.raises(ValidationError):
        ToolCreate(
            name="Convidados",
            tool_key="verificar_convidados_mês",
            webhook_url="https://example.com/webhook",
        )


def test_tool_create_accepts_valid_key():
    payload = ToolCreate(
        name="Convidados",
        tool_key="verificar_convidados_mes",
        webhook_url="https://example.com/webhook",
    )
    assert payload.tool_key == "verificar_convidados_mes"
