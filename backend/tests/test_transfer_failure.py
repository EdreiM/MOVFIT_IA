"""Transferência pra atendente que falha: nunca afirmar "já encaminhei" se
nenhum humano foi avisado, e retentar (na hora e em segundo plano)."""
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models import AiConfig, Conversation, Message, Tool
from app.security import encrypt_secret
from app.services import followup
from app.services.message_flow import (
    _CUSTOMER_TRANSFER_FAILED,
    TOOL_KEY_TRANSFER,
    _tool_result_for_llm,
    execute_tool,
    reply_to_pending_messages,
)


class _FakeResponse:
    def __init__(self, status: int, body: dict | None = None):
        self.status_code = status
        self._body = body or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "Server error '500 Internal Server Error'",
                request=httpx.Request("POST", "https://n8n.test/webhook"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self._body


def _fake_client(responses: list[_FakeResponse]):
    """AsyncClient falso que devolve as respostas na ordem (e conta chamadas)."""
    calls = {"n": 0}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None):
            response = responses[min(calls["n"], len(responses) - 1)]
            calls["n"] += 1
            return response

    return _Client, calls


@pytest_asyncio.fixture
async def transfer_setup(db_session, company):
    config = AiConfig(
        company_id=company.id, ai_name="Mônica", llm_api_key_encrypted=encrypt_secret("sk-test")
    )
    db_session.add(config)
    await db_session.flush()
    tool = Tool(
        company_id=company.id,
        ai_config_id=config.id,
        name="Transferir atendimento",
        tool_key=TOOL_KEY_TRANSFER,
        tool_type="custom",
        webhook_url="https://n8n.test/webhook/transferir",
        is_active=True,
        parameters=[],
    )
    conv = Conversation(
        company_id=company.id,
        contact_phone="5593999887766",
        channel="whatsapp",
        status="open",
        ai_enabled=True,
    )
    db_session.add_all([tool, conv])
    await db_session.flush()
    await db_session.commit()
    return tool, conv


@pytest.mark.asyncio
async def test_transfer_failure_marks_pending_after_retries(db_session, transfer_setup):
    tool, conv = transfer_setup
    client_cls, calls = _fake_client([_FakeResponse(500)])

    with patch("app.services.message_flow.httpx.AsyncClient", client_cls), patch(
        "app.services.message_flow.asyncio.sleep", new_callable=AsyncMock
    ):
        result = await execute_tool(db_session, tool, {"motivo": "Quer trancar"}, conv)

    assert result["sucesso"] is False
    assert calls["n"] == 3  # 1 tentativa + 2 retentativas
    assert conv.transfer_pending_at is not None
    assert conv.transfer_pending_reason == "Quer trancar"
    assert conv.transfer_attempts == 1
    assert conv.status == "open" and conv.ai_enabled is True


@pytest.mark.asyncio
async def test_transfer_recovers_on_transient_error_and_clears_pending(db_session, transfer_setup):
    tool, conv = transfer_setup
    client_cls, calls = _fake_client(
        [_FakeResponse(500), _FakeResponse(200, {"sucesso": True, "mensagem": "ok"})]
    )

    with patch("app.services.message_flow.httpx.AsyncClient", client_cls), patch(
        "app.services.message_flow.asyncio.sleep", new_callable=AsyncMock
    ):
        result = await execute_tool(db_session, tool, {"motivo": "Quer trancar"}, conv)

    assert result["sucesso"] is True
    assert calls["n"] == 2
    assert conv.status == "with_human" and conv.ai_enabled is False
    assert conv.transfer_pending_at is None


def test_failed_transfer_result_never_tells_model_to_claim_success():
    tool = Tool(tool_key=TOOL_KEY_TRANSFER, name="Transferir", tool_type="custom")
    result = _tool_result_for_llm(
        {"sucesso": False, "mensagem": "Falha ao executar a ferramenta agora."}, tool
    )
    assert result["sucesso"] is False
    assert "NÃO diga que" in result["mensagem"]
    assert "já encaminhou" not in result["mensagem"]


@pytest.mark.asyncio
async def test_customer_is_not_told_transfer_happened_when_it_failed(db_session, company, transfer_setup):
    tool, conv = transfer_setup
    db_session.add(
        Message(
            conversation_id=conv.id, company_id=company.id, direction="inbound",
            actor="customer", content_type="text", text="Quero trancar minha matrícula",
        )
    )
    await db_session.commit()

    async def fake_generate(db, conversation, text):
        # A IA tenta transferir (falha) e mesmo assim escreve que encaminhou.
        client_cls, _ = _fake_client([_FakeResponse(500)])
        with patch("app.services.message_flow.httpx.AsyncClient", client_cls), patch(
            "app.services.message_flow.asyncio.sleep", new_callable=AsyncMock
        ):
            await execute_tool(db, tool, {"motivo": "Trancar matrícula"}, conversation)
        return "Já encaminhei seu caso para um atendente, aguarde um instante.", None, False

    with patch("app.services.message_flow.generate_ai_reply", side_effect=fake_generate), patch(
        "app.services.message_flow.send_outbound", new_callable=AsyncMock
    ) as send:
        await reply_to_pending_messages(db_session, conv.id, company.id)
        await db_session.commit()

    sent = " ".join(call.args[3] for call in send.await_args_list)
    assert "registrado" in sent
    assert "encaminhei" not in sent
    saved = (
        await db_session.execute(
            select(Message).where(Message.conversation_id == conv.id, Message.actor == "ai")
        )
    ).scalars().all()
    assert [m.text for m in saved] == [_CUSTOMER_TRANSFER_FAILED]
    await db_session.refresh(conv)
    assert conv.transfer_pending_at is not None


@pytest.mark.asyncio
async def test_background_retry_completes_pending_transfer(db_session, transfer_setup):
    tool, conv = transfer_setup
    from datetime import datetime, timezone

    conv.transfer_pending_at = datetime.now(timezone.utc)
    conv.transfer_pending_reason = "Quer trancar"
    conv.transfer_attempts = 1
    await db_session.commit()

    client_cls, _ = _fake_client([_FakeResponse(200, {"sucesso": True, "mensagem": "ok"})])
    with patch("app.services.message_flow.httpx.AsyncClient", client_cls):
        done = await followup.retry_pending_transfers(db_session)

    await db_session.refresh(conv)
    assert done == 1
    assert conv.status == "with_human"
    assert conv.transfer_pending_at is None


@pytest.mark.asyncio
async def test_background_retry_gives_up_after_max_attempts(db_session, transfer_setup):
    tool, conv = transfer_setup
    from datetime import datetime, timezone

    conv.transfer_pending_at = datetime.now(timezone.utc)
    conv.transfer_attempts = followup.MAX_TRANSFER_RETRY_CALLS
    await db_session.commit()

    client_cls, calls = _fake_client([_FakeResponse(200, {"sucesso": True})])
    with patch("app.services.message_flow.httpx.AsyncClient", client_cls):
        done = await followup.retry_pending_transfers(db_session)

    assert done == 0
    assert calls["n"] == 0
