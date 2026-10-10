"""Integração com a Pacto: conexão OAuth, cliente MCP, resumo de movimento e
o que chega no prompt da IA (app/services/pacto.py)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
import pytest_asyncio

from app.models import AiConfig, Conversation, PactoConnection, PactoUnit, Unit
from app.security import decrypt_secret, encrypt_secret
from app.services import pacto
from app.services.message_flow import generate_ai_reply

# % de acessos por hora de Novo Progresso (10/09 a 09/10/2026), como veio da Pacto.
NOVO_PROGRESSO_BY_HOUR = {
    4: 0.63, 5: 9.01, 6: 4.76, 7: 5.34, 8: 4.82, 9: 3.99, 10: 3.19, 11: 2.74, 12: 4.64, 13: 4.81,
    14: 4.65, 15: 6.86, 16: 5.91, 17: 6.52, 18: 10.56, 19: 10.20, 20: 7.65, 21: 2.97, 22: 0.75, 23: 0.09,
}
NOVO_PROGRESSO_BY_WEEKDAY = {
    "Domingo": 1.85, "Segunda-Feira": 13.99, "Terça-Feira": 19.74, "Quarta-Feira": 18.76,
    "Quinta-Feira": 21.10, "Sexta-Feira": 19.74, "Sábado": 4.85,
}
METADATA = {
    "authorization_endpoint": "https://auth.exemplo/oauth/authorize",
    "token_endpoint": "https://auth.exemplo/oauth/token",
    "registration_endpoint": "https://auth.exemplo/register",
}
REDIRECT = "https://painel.exemplo/pacto/callback"


def test_movement_summary_from_real_turnstile_data():
    assert pacto.summarize_movement(NOVO_PROGRESSO_BY_HOUR, NOVO_PROGRESSO_BY_WEEKDAY) == (
        "Mais cheio: por volta das 5h e das 18h às 21h. "
        "Mais tranquilo: das 9h às 12h e por volta das 21h. "
        "Dia mais movimentado: quinta-feira; sábado e domingo são bem mais vazios."
    )
    # Poucos dados (unidade recém-aberta, catraca sem registro): não inventa resumo.
    assert pacto.summarize_movement({18: 60.0, 19: 40.0}) is None


def test_units_are_matched_by_name_including_abbreviations():
    pacto_names = {
        "np": "MOVFIT N. PROGRESSO - PA | 0006",
        "med": "MOVFIT MEDICILANDIA - PA | 0001",
        "nr": "MOVFIT STM / NOVA REP. - PA | 0005",
        "stm": "MOVFIT SANTAREM - PA | 0003",
        "itb": "MOVFIT ITAITUBA - PA | 0002",
    }
    ours = {
        "1": "Santarém - 24 horas", "2": "Santarém - Nova República", "3": "Itaituba",
        "4": "Medicilândia", "5": "Novo Progresso",
    }
    assert pacto.match_units(pacto_names, ours) == {"np": "5", "med": "4", "nr": "2", "stm": "1", "itb": "3"}
    assert pacto.match_units({"x": "MOVFIT ALTAMIRA - PA"}, ours) == {"x": None}


def test_mcp_responses_are_parsed_from_json_and_sse():
    payload = {"jsonrpc": "2.0", "id": 3, "result": {"content": [{"type": "text", "text": '{"total": 5}'}]}}
    as_json = MagicMock(headers={"content-type": "application/json"})
    as_json.json.return_value = payload
    assert pacto._tool_result(pacto._parse_rpc_response(as_json, 3)) == {"total": 5}

    import json

    as_sse = MagicMock(headers={"content-type": "text/event-stream"})
    as_sse.text = f"event: message\ndata: {json.dumps(payload)}\n\n"
    assert pacto._tool_result(pacto._parse_rpc_response(as_sse, 3)) == {"total": 5}

    structured = {"result": {"structuredContent": {"result": {"total": 7}}, "content": []}}
    assert pacto._tool_result(structured) == {"total": 7}
    with pytest.raises(pacto.PactoError):
        pacto._tool_result({"result": {"isError": True, "content": [{"type": "text", "text": "sem permissão"}]}})


def test_live_occupancy_question_detection():
    assert pacto.asks_live_occupancy("tá cheio agora?") is True
    assert pacto.asks_live_occupancy("como tá o movimento hoje") is True
    assert pacto.asks_live_occupancy("qual o horário mais vazio?") is False
    assert pacto.asks_live_occupancy("quero me matricular agora") is False


def _http_client(response):
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post = AsyncMock(return_value=response)
    return patch("app.services.pacto.httpx.AsyncClient", return_value=client), client


@pytest.mark.asyncio
async def test_connect_registers_once_and_builds_a_pkce_authorization_url(db_session, company):
    registration = MagicMock(status_code=201)
    registration.json.return_value = {"client_id": "cliente-123"}
    http, client = _http_client(registration)

    with patch("app.services.pacto._auth_server_metadata", new_callable=AsyncMock, return_value=METADATA), http:
        url = await pacto.start_authorization(db_session, company.id, REDIRECT)
        await pacto.start_authorization(db_session, company.id, REDIRECT)

    assert client.post.await_count == 1  # mesmo endereço de retorno: não registra de novo
    connection = await pacto.get_connection(db_session, company.id)
    query = parse_qs(urlparse(url).query)
    assert url.startswith("https://auth.exemplo/oauth/authorize?")
    assert query["client_id"] == ["cliente-123"] and query["redirect_uri"] == [REDIRECT]
    assert query["code_challenge_method"] == ["S256"] and query["resource"] == [pacto.MCP_URL]
    assert connection.status == "disconnected"


@pytest.mark.asyncio
async def test_callback_rejects_wrong_state_and_stores_tokens_encrypted(db_session, company):
    connection = PactoConnection(
        company_id=company.id, client_id="cliente-123", redirect_uri=REDIRECT,
        pending_state="estado-certo", pending_code_verifier="verificador",
    )
    db_session.add(connection)
    await db_session.commit()

    with pytest.raises(pacto.PactoError):
        await pacto.finish_authorization(db_session, company.id, "codigo", "estado-errado")

    tokens = {"access_token": "acesso-1", "refresh_token": "renova-1", "expires_in": 3600}
    with patch("app.services.pacto._token_request", new_callable=AsyncMock, return_value=tokens) as token_request, patch(
        "app.services.pacto.discover_units", new_callable=AsyncMock
    ):
        await pacto.finish_authorization(db_session, company.id, "codigo", "estado-certo")

    sent = token_request.await_args.args[1]
    assert sent["grant_type"] == "authorization_code" and sent["code_verifier"] == "verificador"
    assert connection.status == "connected" and connection.pending_state is None
    assert connection.access_token_encrypted != "acesso-1"
    assert decrypt_secret(connection.access_token_encrypted) == "acesso-1"
    assert decrypt_secret(connection.refresh_token_encrypted) == "renova-1"


async def _connected(db_session, company, *, expires_in_seconds: int = 3600, **fields) -> PactoConnection:
    connection = PactoConnection(
        company_id=company.id, client_id="cliente-123", redirect_uri=REDIRECT, status="connected",
        access_token_encrypted=encrypt_secret("acesso-antigo"),
        refresh_token_encrypted=encrypt_secret("renova-1"),
        token_expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds),
        **fields,
    )
    db_session.add(connection)
    await db_session.commit()
    return connection


@pytest.mark.asyncio
async def test_expired_token_is_refreshed_and_lost_access_marks_the_connection(db_session, company):
    connection = await _connected(db_session, company, expires_in_seconds=-10)

    fresh = {"access_token": "acesso-novo", "expires_in": 3600}
    with patch("app.services.pacto._token_request", new_callable=AsyncMock, return_value=fresh) as token_request:
        assert await pacto._valid_access_token(db_session, connection) == "acesso-novo"
        assert await pacto._valid_access_token(db_session, connection) == "acesso-novo"
    assert token_request.await_count == 1  # token válido não renova de novo
    assert decrypt_secret(connection.refresh_token_encrypted) == "renova-1"  # mantido se não vier um novo

    connection.token_expires_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    with patch(
        "app.services.pacto._token_request", new_callable=AsyncMock, side_effect=pacto.PactoError("revogado")
    ):
        with pytest.raises(pacto.PactoError):
            await pacto._valid_access_token(db_session, connection)
    assert connection.status == "error" and "revogado" in connection.last_error


class _FakeSession:
    """Faz o papel do servidor da Pacto nas chamadas de relatório."""

    def __init__(self, *_args, **_kwargs):
        self.calls: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        arguments = arguments or {}
        if name == "listar_empresas_usuario":
            return {"empresas": [{"chave_empresa": "chave-np", "nome_empresa": "ACAD. MOVFIT CT UNID. 06"}]}
        if name == "listar_unidades_empresa":
            return {"unidades": [{"empresa_id": 1, "nome": "MOVFIT N. PROGRESSO - PA | 0006"}]}
        if name == "totalizador_acessos_empresa" and arguments["frequencia"] == "GERAL_POR_HORARIO":
            return {"linhas": [{"hora": h, "porcentagem": f"{p:.2f} %".replace(".", ",")} for h, p in NOVO_PROGRESSO_BY_HOUR.items()]}
        if name == "totalizador_acessos_empresa":
            return {"linhas": [{"dia_da_semana": d, "porcentagem": f"{p:.2f} %".replace(".", ",")} for d, p in NOVO_PROGRESSO_BY_WEEKDAY.items()]}
        if name == "gestao_acessos_empresa":
            return {"alunos_em_tempo_real": 8, "momento": "2026-10-09 10:40"}
        raise AssertionError(f"ferramenta inesperada: {name}")


@pytest_asyncio.fixture
async def novo_progresso(db_session, company) -> Unit:
    unit = Unit(company_id=company.id, name="Novo Progresso", city="Novo Progresso")
    db_session.add(unit)
    await db_session.commit()
    return unit


@pytest.mark.asyncio
async def test_sync_discovers_units_and_writes_the_movement_text(db_session, company, novo_progresso):
    connection = await _connected(db_session, company)
    pacto._live_cache.clear()

    with patch("app.services.pacto.PactoSession", _FakeSession):
        (pacto_unit,) = await pacto.discover_units(db_session, connection)
        assert pacto_unit.unit_id == novo_progresso.id  # vínculo sugerido pelo nome
        assert await pacto.sync_movement(db_session, connection) == 1
        await db_session.commit()

        assert pacto_unit.movement_text.startswith("Mais cheio: por volta das 5h e das 18h às 21h.")
        assert connection.last_sync_at is not None
        block = await pacto.build_movement_prompt_block(db_session, company.id)
        assert "- Novo Progresso: Mais cheio" in block
        note = await pacto.live_occupancy_note(db_session, company.id, novo_progresso)
        assert "8 aluno(s)" in note and "10:40" in note and "Novo Progresso" in note

    # Desligada no painel, ou com o acesso caído: a IA não recebe nada.
    connection.enabled = False
    assert await pacto.build_movement_prompt_block(db_session, company.id) == ""
    connection.enabled, connection.status = True, "error"
    assert await pacto.build_movement_prompt_block(db_session, company.id) == ""


async def _ai_reply(db_session, company, user_text: str):
    db_session.add(AiConfig(company_id=company.id, ai_name="Mônica", llm_api_key_encrypted=encrypt_secret("sk-test")))
    conv = Conversation(
        company_id=company.id, contact_phone="5593999887755", channel="test_console", status="open", ai_enabled=True
    )
    db_session.add(conv)
    await db_session.commit()
    with patch("app.services.message_flow.fetch_rag_context", new_callable=AsyncMock, return_value=""), patch(
        "app.services.message_flow.chat_completion",
        new_callable=AsyncMock,
        return_value={"content": "Agora tem 8 pessoas por lá.", "tool_calls": None},
    ) as llm_mock:
        reply, _, _ = await generate_ai_reply(db_session, conv, user_text)
    system = "\n".join(m["content"] for m in llm_mock.await_args_list[0].kwargs["messages"] if m["role"] == "system")
    return reply, system


@pytest.mark.asyncio
async def test_ai_receives_movement_and_live_occupancy(db_session, company, novo_progresso):
    await _connected(db_session, company)
    db_session.add(
        PactoUnit(
            company_id=company.id, chave_empresa="chave-np", empresa_id=1, pacto_name="MOVFIT N. PROGRESSO",
            unit_id=novo_progresso.id, movement_text="Mais cheio: das 18h às 21h.",
        )
    )
    await db_session.commit()
    pacto._live_cache.clear()

    with patch("app.services.pacto.PactoSession", _FakeSession):
        _, system = await _ai_reply(db_session, company, "tá cheio agora em Novo Progresso?")

    assert "[Movimento nas unidades" in system and "- Novo Progresso: Mais cheio: das 18h às 21h." in system
    assert "LOTAÇÃO AGORA (10:40) na unidade Novo Progresso" in system


@pytest.mark.asyncio
async def test_pacto_failure_never_blocks_the_reply(db_session, company, novo_progresso):
    await _connected(db_session, company)
    with patch(
        "app.services.message_flow.pacto_movement_block", new_callable=AsyncMock, side_effect=RuntimeError("Pacto fora do ar")
    ):
        reply, system = await _ai_reply(db_session, company, "tá cheio agora?")

    assert reply == "Agora tem 8 pessoas por lá."
    assert "[Movimento nas unidades" not in system
