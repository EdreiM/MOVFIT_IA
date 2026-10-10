"""Exportação de conversas (.zip) pra revisão de erros da IA."""
import io
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from app.models import Conversation, Message, ToolCallLog
from app.services.conversation_export import (
    export_conversations_zip,
    mask_phone,
    mask_pii,
)


def test_mask_pii_hides_cpf_email_and_phone_but_keeps_context():
    text = "meu cpf é 013.546.672-55, ou 01354667255, email a.b@x.com, zap 5593991095022"
    masked = mask_pii(text)
    assert "01354667255" not in masked
    assert "013.546.672-55" not in masked
    assert "a.b@x.com" not in masked
    assert "5593991095022" not in masked
    assert masked.count("[CPF]") == 2
    assert "[email]" in masked and "[telefone]" in masked
    assert "meu cpf é" in masked


def test_mask_pii_keeps_short_numbers_like_times_and_prices():
    assert mask_pii("às 21:30, R$ 197,00, opção 3") == "às 21:30, R$ 197,00, opção 3"


def test_mask_phone_keeps_only_ddd_and_last_digits():
    assert mask_phone("+5593991095022") == "5593****5022"


async def _conv(db_session, company, phone, *, channel="whatsapp", last_days_ago=0):
    conv = Conversation(
        company_id=company.id,
        contact_phone=phone,
        channel=channel,
        status="open",
        ai_enabled=True,
        last_message_at=datetime.now(timezone.utc) - timedelta(days=last_days_ago),
    )
    db_session.add(conv)
    await db_session.flush()
    return conv


def _read(zip_bytes: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        assert archive.namelist() == ["conversas.txt"]
        return archive.read("conversas.txt").decode("utf-8")


@pytest.mark.asyncio
async def test_export_interleaves_messages_and_tool_calls_in_time_order(db_session, company):
    conv = await _conv(db_session, company, "5593991095022")
    t0 = datetime.now(timezone.utc) - timedelta(minutes=10)
    db_session.add_all(
        [
            Message(
                conversation_id=conv.id, company_id=company.id, direction="inbound",
                actor="customer", content_type="text", text="Meu CPF 01354667255",
                created_at=t0,
            ),
            ToolCallLog(
                company_id=company.id, conversation_id=conv.id,
                tool_key="verificar_unidade_por_cpf", tool_name="Verificar unidade",
                success=False, arguments={"cpf": "01354667255"},
                error_message="timeout", created_at=t0 + timedelta(seconds=5),
            ),
            Message(
                conversation_id=conv.id, company_id=company.id, direction="outbound",
                actor="ai", content_type="text", text="Não consegui verificar agora.",
                created_at=t0 + timedelta(seconds=9),
            ),
        ]
    )
    await db_session.commit()

    content, total = await export_conversations_zip(db_session, company.id, days=7)
    text = _read(content)

    assert total == 1
    assert "CLIENTE: Meu CPF [CPF]" in text
    assert "[ferramenta ✗] verificar_unidade_por_cpf" in text
    assert "timeout" in text
    assert "IA: Não consegui verificar agora." in text
    assert "01354667255" not in text
    assert "5593991095022" not in text
    body = text.split("-" * 78, 1)[1]  # depois do cabeçalho (a legenda também cita "[ferramenta")
    assert body.index("CLIENTE:") < body.index("[ferramenta") < body.index("IA:")


@pytest.mark.asyncio
async def test_export_can_disable_masking(db_session, company):
    conv = await _conv(db_session, company, "5593991095022")
    db_session.add(
        Message(
            conversation_id=conv.id, company_id=company.id, direction="inbound",
            actor="customer", content_type="text", text="cpf 01354667255",
        )
    )
    await db_session.commit()

    content, _ = await export_conversations_zip(db_session, company.id, mask=False)
    text = _read(content)

    assert "cpf 01354667255" in text
    assert "5593991095022" in text


@pytest.mark.asyncio
async def test_export_skips_test_console_and_old_conversations(db_session, company):
    recent = await _conv(db_session, company, "5593990000001")
    await _conv(db_session, company, "5593990000002", channel="test_console")
    await _conv(db_session, company, "5593990000003", last_days_ago=60)
    db_session.add(
        Message(
            conversation_id=recent.id, company_id=company.id, direction="inbound",
            actor="customer", content_type="text", text="oi",
        )
    )
    await db_session.commit()

    content, total = await export_conversations_zip(db_session, company.id, days=30)
    text = _read(content)

    assert total == 1
    assert str(recent.id) in text
    assert "5593****0002" not in text
    assert "5593****0003" not in text


@pytest.mark.asyncio
async def test_export_endpoint_is_not_shadowed_by_conversation_id_route(
    client, db_session, company, user
):
    conv = await _conv(db_session, company, "5593990000009")
    db_session.add(
        Message(
            conversation_id=conv.id, company_id=company.id, direction="inbound",
            actor="customer", content_type="text", text="oi, tudo bem?",
        )
    )
    await db_session.commit()

    login = await client.post(
        "/auth/login", json={"email": "teste@movfit.com", "password": "senha-teste-123"}
    )
    token = login.json()["access_token"]
    res = await client.get(
        "/conversations/export?days=7",
        headers={"Authorization": f"Bearer {token}", "X-Company-Id": str(company.id)},
    )

    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "application/zip"
    assert "attachment" in res.headers["content-disposition"]
    assert res.headers["x-total-conversations"] == "1"
    assert "oi, tudo bem?" in _read(res.content)
