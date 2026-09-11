"""Captura de unidade no Lead via verificar_unidade_por_cpf."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models import AiConfig, Conversation, Lead, Tool
from app.services.message_flow import TOOL_KEY_VERIFY_UNIT_BY_CPF, execute_tool, sanitize_phone_digits


@pytest_asyncio.fixture
async def conversation_with_tool(db_session, company):
    config = AiConfig(company_id=company.id, ai_name="Mônica")
    db_session.add(config)
    await db_session.flush()
    tool = Tool(
        company_id=company.id,
        ai_config_id=config.id,
        name="Verificar unidade",
        tool_key=TOOL_KEY_VERIFY_UNIT_BY_CPF,
        description="Descobre a unidade pelo CPF",
        parameters=[{"name": "cpf", "type": "string", "required": True}],
        webhook_url="https://example.com/webhook/verifica_unidade",
        is_active=True,
    )
    conv = Conversation(
        company_id=company.id,
        contact_phone="5593999887766",
        channel="whatsapp",
        status="open",
        ai_enabled=True,
    )
    db_session.add_all([tool, conv])
    await db_session.commit()
    await db_session.refresh(tool)
    await db_session.refresh(conv)
    return conv, tool


@pytest.mark.asyncio
async def test_verify_unit_persists_unidade_and_is_student(db_session, company, conversation_with_tool):
    conv, tool = conversation_with_tool
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "sucesso": True,
        "mensagem": "Aluno encontrado na unidade Santarém - 24 horas.",
        "dados": {"cpf": "12345678900", "unidade": "Santarém - 24 horas"},
    }

    with patch("app.services.message_flow.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post = AsyncMock(return_value=mock_resp)
        client_cls.return_value = client

        result = await execute_tool(db_session, tool, {"cpf": "12345678900"}, conv)
        await db_session.commit()

    assert result["sucesso"] is True
    phone = sanitize_phone_digits(conv.contact_phone)
    lead = (
        await db_session.execute(select(Lead).where(Lead.company_id == company.id, Lead.phone == phone))
    ).scalar_one()
    assert lead.unit == "Santarém - 24 horas"
    assert lead.cpf == "12345678900"
    assert lead.is_student is True
    assert lead.stage == "aluno"


@pytest.mark.asyncio
async def test_verify_unit_failure_does_not_set_unit(db_session, company, conversation_with_tool):
    conv, tool = conversation_with_tool
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "sucesso": False,
        "mensagem": "CPF não encontrado.",
        "dados": {},
    }

    with patch("app.services.message_flow.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post = AsyncMock(return_value=mock_resp)
        client_cls.return_value = client

        await execute_tool(db_session, tool, {"cpf": "00000000000"}, conv)
        await db_session.commit()

    phone = sanitize_phone_digits(conv.contact_phone)
    lead = (
        await db_session.execute(select(Lead).where(Lead.company_id == company.id, Lead.phone == phone))
    ).scalar_one_or_none()
    if lead:
        assert lead.unit is None
        assert lead.is_student is False
