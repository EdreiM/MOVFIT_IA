"""Cliente manda comprovante de pagamento: vai pro atendente com contexto."""
from unittest.mock import AsyncMock, patch

import pytest

from app.models import Conversation, Tool
from app.services.message_flow import (
    TOOL_KEY_TRANSFER,
    _payment_proof_description,
    _run_payment_proof_pipeline,
)


def test_detects_payment_proof_from_image_description():
    text = (
        "[Imagem enviada pelo cliente] COMPROVANTE DE PAGAMENTO: Pix de R$ 167,00 "
        "para Mov Fit em 09/10/2026."
    )
    description = _payment_proof_description(text)
    assert description is not None
    assert "R$ 167,00" in description


def test_ignores_other_images_and_plain_text():
    assert _payment_proof_description(
        "[Imagem enviada pelo cliente] Foto de uma embalagem de pasta de amendoim."
    ) is None
    assert _payment_proof_description("segue o comprovante de pagamento") is None


def test_detects_proof_inside_a_burst_of_messages():
    burst = "Oi\n[Imagem enviada pelo cliente] COMPROVANTE DE PAGAMENTO: Pix R$ 99,00\nPago"
    assert _payment_proof_description(burst) is not None


@pytest.mark.asyncio
async def test_pipeline_transfers_with_image_context(db_session):
    conv = Conversation(
        contact_phone="5593999000111", channel="whatsapp", status="open", ai_enabled=True
    )
    tool = Tool(tool_key=TOOL_KEY_TRANSFER, name="Transferir", webhook_url="https://n8n.test/t")

    with patch(
        "app.services.message_flow.execute_tool", new_callable=AsyncMock, return_value={"sucesso": True}
    ) as execute:
        reply = await _run_payment_proof_pipeline(
            db_session, conv, {TOOL_KEY_TRANSFER: tool}, "COMPROVANTE DE PAGAMENTO: Pix R$ 167,00"
        )

    motivo = execute.await_args.args[2]["motivo"]
    assert "comprovante" in motivo.lower()
    assert "R$ 167,00" in motivo
    assert "Recebi seu comprovante" in reply


@pytest.mark.asyncio
async def test_pipeline_without_transfer_tool_still_answers(db_session):
    conv = Conversation(
        contact_phone="5593999000111", channel="whatsapp", status="open", ai_enabled=True
    )
    reply = await _run_payment_proof_pipeline(db_session, conv, {}, "COMPROVANTE DE PAGAMENTO: x")
    assert "recepção" in reply
