"""Auditoria amostral de atendimentos para vitrine de métricas."""
from datetime import datetime, timezone

import pytest

from app.models import AiConfig, Conversation, Message, Tool, ToolCallLog
from app.services.metrics_showcase import _anonymize_text, compute_metrics_showcase


def test_anonymize_text_masks_cpf_and_phone():
    text = "Meu CPF é 12345678901 e meu zap 5593999887766"
    masked = _anonymize_text(text)
    assert "12345678901" not in masked
    assert "5593999887766" not in masked


@pytest.mark.asyncio
async def test_showcase_returns_plan_example(db_session, company):
    conv = Conversation(
        company_id=company.id,
        contact_phone="5511999000001",
        status="resolved",
        ai_enabled=True,
        channel="whatsapp",
    )
    config = AiConfig(company_id=company.id)
    db_session.add(config)
    db_session.add(conv)
    await db_session.flush()

    tool = Tool(
        company_id=company.id,
        ai_config_id=config.id,
        tool_key="enviar_imagens_planos",
        name="Enviar planos",
        webhook_url="https://example.com/plans",
        is_active=True,
    )
    db_session.add(tool)
    await db_session.flush()

    db_session.add_all(
        [
            Message(
                company_id=company.id,
                conversation_id=conv.id,
                direction="inbound",
                actor="customer",
                content_type="text",
                text="Quais são os planos da unidade?",
            ),
            Message(
                company_id=company.id,
                conversation_id=conv.id,
                direction="outbound",
                actor="ai",
                content_type="text",
                text="Aqui estão os planos disponíveis para você.",
            ),
            Message(
                company_id=company.id,
                conversation_id=conv.id,
                direction="outbound",
                actor="ai",
                content_type="image",
                text="🏋️ Plano mensal",
                raw_payload={"images": [{"plano": "Mensal Recorrente", "unidade": "Centro"}]},
            ),
        ]
    )
    db_session.add(
        ToolCallLog(
            company_id=company.id,
            conversation_id=conv.id,
            tool_id=tool.id,
            tool_key=tool.tool_key,
            tool_name=tool.name,
            success=True,
            arguments={"unidade": "Centro"},
            created_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()

    examples = await compute_metrics_showcase(db_session, company.id)

    assert any(example.modality == "plans" for example in examples)
    plan = next(example for example in examples if example.modality == "plans")
    assert len(plan.snippets) >= 2
    assert plan.evidence
    assert plan.outcome
    assert "desfecho correto verificado" in plan.evidence
    assert any("planos" in snippet.text.lower() or "plano" in snippet.text.lower() for snippet in plan.snippets)


@pytest.mark.asyncio
async def test_showcase_excludes_transferred_conversations(db_session, company):
    config = AiConfig(company_id=company.id)
    db_session.add(config)
    conv = Conversation(
        company_id=company.id,
        contact_phone="5511999000002",
        status="with_human",
        ai_enabled=False,
        channel="whatsapp",
    )
    db_session.add(conv)
    await db_session.flush()

    tool = Tool(
        company_id=company.id,
        ai_config_id=config.id,
        tool_key="enviar_imagens_planos",
        name="Enviar planos",
        webhook_url="https://example.com/plans",
        is_active=True,
    )
    db_session.add(tool)
    await db_session.flush()

    db_session.add_all(
        [
            Message(
                company_id=company.id,
                conversation_id=conv.id,
                direction="inbound",
                actor="customer",
                content_type="text",
                text="Quero ver os planos",
            ),
            Message(
                company_id=company.id,
                conversation_id=conv.id,
                direction="outbound",
                actor="ai",
                content_type="image",
                text="Plano",
                raw_payload={"images": [{"plano": "Mensal"}]},
            ),
        ]
    )
    db_session.add(
        ToolCallLog(
            company_id=company.id,
            conversation_id=conv.id,
            tool_id=tool.id,
            tool_key=tool.tool_key,
            tool_name=tool.name,
            success=True,
            arguments={},
            created_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()

    examples = await compute_metrics_showcase(db_session, company.id)
    assert not any(example.modality == "plans" for example in examples)
