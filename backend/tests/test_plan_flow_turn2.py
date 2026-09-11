"""Regressão: planos no turno 1 + unidade no turno 2 não pode morrer em silêncio."""
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from app.models import Message, Tool
from app.services.message_flow import (
    TOOL_KEY_SEND_PLAN_IMAGES,
    TOOL_KEY_VERIFY_UNIT_BY_CPF,
    _history_text_for_llm,
    _is_guest_info_question,
    _is_valid_openai_tool,
    _plan_flow_active,
    _safe_tool_result_json,
    _student_operational_action,
    _tool_to_openai_schema,
    _wants_plan_info,
    _wants_student_action,
)


def test_wants_plan_info_from_recent_customer_message():
    assert _wants_plan_info("Santarém - 24 horas") is False
    assert _wants_plan_info(
        "Santarém - 24 horas",
        recent_customer_texts=["oi, quero ver os planos"],
    ) is True


def test_plan_flow_not_active_when_topic_changes_to_hours():
    assert _plan_flow_active(
        "Quais os horarios de funcionamento?",
        ["quero ver os planos", "Santarém - 24 horas"],
    ) is False


def test_student_action_detects_overdue_installments():
    assert _wants_student_action("Quero ver as parcelas atrasadas") is True


def test_guest_info_question_is_not_operational_student_action():
    assert _is_guest_info_question("Entendi, e para levar convidados?") is True
    assert _student_operational_action("Entendi, e para levar convidados?") is False


def test_counting_guests_is_operational():
    assert _is_guest_info_question("Quantos convidados posso levar esse mês?") is False
    assert _student_operational_action("Quantos convidados posso levar esse mês?") is True


def test_history_compacts_long_plan_captions():
    long_caption = "🏋️ *PLANO ANUAL PARCELADO*\n\n" + ("benefício " * 200)
    msg = Message(
        conversation_id=__import__("uuid").uuid4(),
        company_id=__import__("uuid").uuid4(),
        direction="outbound",
        actor="ai",
        content_type="text",
        text=long_caption,
    )
    compact = _history_text_for_llm(msg)
    assert compact is not None
    assert len(compact) < 200
    assert "já enviada" in compact


def test_safe_tool_result_json_handles_non_serializable():
    raw = _safe_tool_result_json({"sucesso": True, "quando": __import__("datetime").datetime.now()})
    assert "sucesso" in raw


@pytest.mark.asyncio
async def test_generate_ai_reply_does_not_crash_on_guest_info_with_verify_tool(
    db_session, company
):
    """Regressão: lead era usado antes de existir → NameError silencioso no debounce."""
    from app.models import AiConfig, Conversation, Tool
    from app.security import encrypt_secret
    from app.services.message_flow import generate_ai_reply

    config = AiConfig(
        company_id=company.id,
        ai_name="Mônica",
        llm_api_key_encrypted=encrypt_secret("sk-test"),
    )
    db_session.add(config)
    await db_session.flush()
    tool = Tool(
        company_id=company.id,
        ai_config_id=config.id,
        name="Verificar unidade",
        tool_key=TOOL_KEY_VERIFY_UNIT_BY_CPF,
        description="Descobre unidade",
        parameters=[{"name": "cpf", "type": "string", "required": True}],
        webhook_url="https://example.com/hook",
        is_active=True,
    )
    conv = Conversation(
        company_id=company.id,
        contact_phone="5593999887766",
        channel="test_console",
        status="open",
        ai_enabled=True,
    )
    db_session.add_all([tool, conv])
    await db_session.commit()

    with patch(
        "app.services.message_flow.fetch_rag_context",
        new_callable=AsyncMock,
        return_value="Convidados: alunos podem levar até 4 convidados por mês.",
    ), patch(
        "app.services.message_flow.chat_completion",
        new_callable=AsyncMock,
        return_value={"content": "Você pode levar convidados! Já é aluno?", "tool_calls": None},
    ):
        reply, _, _ = await generate_ai_reply(
            db_session, conv, "Entendi, e para levar convidados?"
        )

    assert reply == "Você pode levar convidados! Já é aluno?"


def test_invalid_tool_key_is_filtered_from_openai_schema():
    bad = Tool(
        name="Convidados",
        tool_key="verificar_convidados_mês",
        description="x",
        parameters=[{"name": "cpf", "type": "string", "required": True}],
        webhook_url="https://example.com/hook",
    )
    good = Tool(
        name="Planos",
        tool_key=TOOL_KEY_SEND_PLAN_IMAGES,
        description="x",
        parameters=[{"name": "Unidade", "type": "string", "required": True}],
        webhook_url="https://example.com/hook",
    )
    assert _is_valid_openai_tool(bad) is False
    offered = [good, bad]
    tool_defs = [
        _tool_to_openai_schema(t) for t in offered if _is_valid_openai_tool(t)
    ]
    assert len(tool_defs) == 1
    assert tool_defs[0]["function"]["name"] == TOOL_KEY_SEND_PLAN_IMAGES


@pytest_asyncio.fixture
async def unit_with_plans(db_session, company):
    from app.models import Plan, Unit

    unit = Unit(company_id=company.id, name="Santarém - 24 horas", city="Santarém", unit_type="Premium")
    db_session.add(unit)
    await db_session.flush()

    plan_anual = Plan(
        company_id=company.id,
        unit_id=unit.id,
        name="Plano Anual Parcelado",
        monthly_price=197.0,
        image_url="https://exemplo.com/anual.png",
    )
    plan_mensal = Plan(
        company_id=company.id,
        unit_id=unit.id,
        name="Plano Mensal Recorrente",
        monthly_price=217.0,
        image_url="https://exemplo.com/mensal.png",
    )
    db_session.add_all([plan_anual, plan_mensal])
    await db_session.commit()
    return unit, plan_anual, plan_mensal


@pytest.mark.asyncio
async def test_auto_send_plan_images_fires_on_unit_only_after_plan_request(
    db_session, company, unit_with_plans
):
    from app.models import AiConfig, Conversation, Message, Tool
    from app.services.message_flow import _auto_send_plan_images

    unit, plan_anual, _plan_mensal = unit_with_plans
    config = AiConfig(
        company_id=company.id,
        ai_name="Mônica",
    )
    db_session.add(config)
    await db_session.flush()
    tool = Tool(
        company_id=company.id,
        ai_config_id=config.id,
        name="Imagens planos",
        tool_key=TOOL_KEY_SEND_PLAN_IMAGES,
        description="Envia imagens",
        parameters=[{"name": "Unidade", "type": "string", "required": True}],
        webhook_url="https://example.com/hook",
        is_active=True,
    )
    conv = Conversation(
        company_id=company.id,
        contact_phone="5593999887766",
        channel="test_console",
        status="open",
        ai_enabled=True,
    )
    db_session.add_all([tool, conv])
    await db_session.flush()

    db_session.add(
        Message(
            conversation_id=conv.id,
            company_id=company.id,
            direction="inbound",
            actor="customer",
            content_type="text",
            text="quero ver os planos",
        )
    )
    await db_session.commit()

    tools_by_key = {tool.tool_key: tool}
    touched: set[str] = set()

    with patch(
        "app.services.message_flow._present_plan_images_with_captions",
        new_callable=AsyncMock,
    ) as present_mock:
        present_mock.return_value = {"sucesso": True, "mensagem": "ok", "dados": {}}
        await _auto_send_plan_images(
            db_session,
            conv,
            "Santarém - 24 horas",
            "",
            tools_by_key,
            touched,
            recent_customer_texts=["quero ver os planos"],
        )

    present_mock.assert_awaited_once()
    assert present_mock.await_args.args[4][0]["unidade"] == unit.name
