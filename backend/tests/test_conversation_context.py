"""Assunto ativo e resumo de histórico para o LLM."""
import uuid

from app.models import Conversation, Lead, Message
from app.services.conversation_context import (
    build_active_topic_system_block,
    build_history_summary_system_block,
    infer_active_topic_label,
    message_preview_text,
    trim_history_for_llm,
)


def test_infer_active_topic_bioimpedancia():
    label = infer_active_topic_label(
        user_text="Sim",
        recent_customer_texts=["Queria marca minha Bioimpedância"],
        history=[],
        cancellation_active=False,
        app_support_active=False,
        physical_eval_context=True,
        plan_intent_active=False,
        tour_discussed=True,
        tour_explicit=False,
        student_operational=False,
        promotion_transfer=False,
    )
    assert "avaliação física" in label or "bioimped" in label


def test_active_topic_block_mentions_priority():
    block = build_active_topic_system_block("planos, preços ou matrícula")
    assert "Assunto ativo" in block
    assert "planos" in block


def test_history_summary_when_long_enough():
    conv_id = uuid.uuid4()
    company_id = uuid.uuid4()
    history = []
    for i in range(10):
        history.append(
            Message(
                company_id=company_id,
                conversation_id=conv_id,
                actor="customer" if i % 2 == 0 else "ai",
                direction="inbound" if i % 2 == 0 else "outbound",
                text=f"mensagem {i}",
            )
        )
    lead = Lead(company_id=company_id, phone="559999", unit="Itaituba", name="Rejane Silva")
    conv = Conversation(
        company_id=company_id,
        contact_phone="559999",
        channel="whatsapp",
        status="open",
    )
    summary = build_history_summary_system_block(history, lead, conv)
    assert summary is not None
    assert "Itaituba" in summary


def test_trim_history_keeps_tail():
    msgs = [
        Message(
            company_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            actor="customer",
            direction="inbound",
            text=str(i),
        )
        for i in range(25)
    ]
    trimmed = trim_history_for_llm(msgs)
    assert len(trimmed) == 12
    assert trimmed[-1].text == "24"


def test_message_preview_truncates():
    long = "a" * 200
    assert message_preview_text(long) is not None
    assert len(message_preview_text(long) or "") <= 120
