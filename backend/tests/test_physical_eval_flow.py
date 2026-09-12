"""Fluxo determinístico: avaliação física → CPF → unidade → dia → horários."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.services.message_flow import (
    TOOL_KEY_CHECK_SCHEDULE,
    TOOL_KEY_VERIFY_UNIT_BY_CPF,
    _enrich_schedule_result_with_preference,
    _extract_schedule_date_from_text,
    _extract_schedule_period_from_text,
    _extract_schedule_time_from_text,
    _filter_horarios_by_preference,
    _format_schedule_reply_from_dados,
    _is_physical_eval_intent,
    _physical_eval_followup,
    _physical_eval_schedule_preference,
    _physical_eval_weekend_reply,
    _run_physical_eval_pipeline,
    _schedule_date_is_weekend,
)


def test_physical_eval_intent_detected():
    assert _is_physical_eval_intent("Quero agendar avaliação física") is True
    assert _is_physical_eval_intent("Preciso marcar minha avaliação") is True
    assert _is_physical_eval_intent("Quais os horários de funcionamento?") is False


def test_extract_schedule_date_formats():
    ref = datetime(2026, 9, 11, 10, 0, tzinfo=timezone(timedelta(hours=-3)))
    assert _extract_schedule_date_from_text("20260915", ref) == "20260915"
    assert _extract_schedule_date_from_text("15/09/2026", ref) == "20260915"
    assert _extract_schedule_date_from_text("amanhã", ref) == "20260912"


def test_weekend_detection():
    assert _schedule_date_is_weekend("20260912") is True  # sábado
    assert _schedule_date_is_weekend("20260913") is True  # domingo
    assert _schedule_date_is_weekend("20260915") is False  # terça


def test_weekend_reply_uses_first_name():
    from app.models import Lead

    lead = Lead(company_id=__import__("uuid").uuid4(), phone="559999", name="MARIA SILVA")
    reply = _physical_eval_weekend_reply("20260913", lead)
    assert "MARIA" in reply
    assert "segunda a sexta" in reply.lower() or "segunda" in reply.lower()


def test_physical_eval_followup_after_intent():
    assert _physical_eval_followup(
        "52998224725",
        ["Quero agendar avaliação física"],
    ) is True
    assert _physical_eval_followup(
        "amanhã",
        ["Quero agendar avaliação física", "52998224725"],
    ) is True
    assert _physical_eval_followup(
        "então terça",
        ["Quero agendar avaliação física", "52998224725", "segunda de manhã"],
    ) is True


def test_extract_period_and_time():
    assert _extract_schedule_period_from_text("segunda de manhã") == "manha"
    assert _extract_schedule_period_from_text("quarta à tarde") == "tarde"
    assert _extract_schedule_time_from_text("terça 9 h") == "09:00"
    assert _extract_schedule_time_from_text("às 14:30") == "14:30"


def test_schedule_preference_inherits_period_from_context():
    ref = datetime(2026, 9, 11, 10, 0, tzinfo=timezone(timedelta(hours=-3)))
    pref = _physical_eval_schedule_preference(
        "então terça",
        ["Quero agendar avaliação física", "segunda de manhã"],
        ref,
    )
    assert pref["date"] is not None
    assert pref["period"] == "manha"


def test_filter_morning_slots_and_missing_preferred():
    horarios = ["08:00", "09:30", "11:00", "14:00", "16:00"]
    filtered, meta = _filter_horarios_by_preference(
        horarios, period="manha", preferred_time="09:00"
    )
    assert filtered == ["08:00", "09:30", "11:00"]
    assert meta["horario_preferido_indisponivel"] == "09:00"


def test_enrich_schedule_result_filters_afternoon():
    raw = {
        "sucesso": True,
        "dados": {
            "horarios_disponiveis": ["08:00", "09:00", "14:00", "15:00"],
            "data_formatada": "15/09/2026",
        },
    }
    enriched = _enrich_schedule_result_with_preference(
        raw, period="tarde", preferred_time=None
    )
    assert enriched["dados"]["horarios_disponiveis"] == ["14:00", "15:00"]


def test_schedule_reply_notes_unavailable_preferred_time():
    reply = _format_schedule_reply_from_dados(
        {
            "data_formatada": "15/09/2026",
            "unidade": "Santarém - 24 horas",
            "periodo_solicitado_label": "manhã",
            "horario_preferido_indisponivel": "09:00",
            "horarios_disponiveis": ["08:00", "10:00"],
        }
    )
    assert "09:00" in reply
    assert "08:00" in reply


def test_schedule_reply_from_dados():
    reply = _format_schedule_reply_from_dados(
        {
            "unidade": "Santarém - 24 horas",
            "data_formatada": "15/09/2026",
            "horarios_disponiveis": ["09:00", "09:30", "11:00"],
            "tipo_agendamento": "Avaliação física",
        }
    )
    assert "15/09/2026" in reply
    assert "09:00" in reply
    assert "Qual prefere" in reply


@pytest.mark.asyncio
async def test_physical_eval_pipeline_asks_cpf(db_session, company):
    from app.models import AiConfig, Conversation, Tool
    from app.security import encrypt_secret

    config = AiConfig(
        company_id=company.id,
        ai_name="Mônica",
        llm_api_key_encrypted=encrypt_secret("sk-test"),
    )
    db_session.add(config)
    await db_session.flush()
    verify = Tool(
        company_id=company.id,
        ai_config_id=config.id,
        name="Verificar unidade",
        tool_key=TOOL_KEY_VERIFY_UNIT_BY_CPF,
        description="Descobre unidade",
        parameters=[{"name": "cpf", "type": "string", "required": True}],
        webhook_url="https://example.com/verify",
        is_active=True,
    )
    schedule = Tool(
        company_id=company.id,
        ai_config_id=config.id,
        name="Consulta horários",
        tool_key=TOOL_KEY_CHECK_SCHEDULE,
        description="Consulta horários",
        parameters=[
            {"name": "unidade", "type": "string", "required": True},
            {"name": "data", "type": "string", "required": True},
        ],
        webhook_url="https://example.com/schedule",
        is_active=True,
    )
    conv = Conversation(
        company_id=company.id,
        contact_phone="5593999887766",
        channel="whatsapp",
        status="open",
        ai_enabled=True,
    )
    db_session.add_all([verify, schedule, conv])
    await db_session.commit()

    tools_by_key = {verify.tool_key: verify, schedule.tool_key: schedule}
    reply, _ = await _run_physical_eval_pipeline(
        db_session,
        conv,
        "Quero agendar avaliação física",
        tools_by_key,
        None,
        config=config,
        is_first_contact=True,
        ai_name="Mônica",
    )

    assert reply is not None
    assert "CPF" in reply
    assert "unidade" not in reply.lower()


@pytest.mark.asyncio
async def test_physical_eval_pipeline_asks_day_after_unit(db_session, company):
    from app.models import Conversation, Lead, Tool
    from app.models import AiConfig
    from app.security import encrypt_secret

    config = AiConfig(
        company_id=company.id,
        ai_name="Mônica",
        llm_api_key_encrypted=encrypt_secret("sk-test"),
    )
    db_session.add(config)
    await db_session.flush()
    verify = Tool(
        company_id=company.id,
        ai_config_id=config.id,
        name="Verificar unidade",
        tool_key=TOOL_KEY_VERIFY_UNIT_BY_CPF,
        description="Descobre unidade",
        parameters=[{"name": "cpf", "type": "string", "required": True}],
        webhook_url="https://example.com/verify",
        is_active=True,
    )
    schedule = Tool(
        company_id=company.id,
        ai_config_id=config.id,
        name="Consulta horários",
        tool_key=TOOL_KEY_CHECK_SCHEDULE,
        description="Consulta horários",
        parameters=[
            {"name": "unidade", "type": "string", "required": True},
            {"name": "data", "type": "string", "required": True},
        ],
        webhook_url="https://example.com/schedule",
        is_active=True,
    )
    conv = Conversation(
        company_id=company.id,
        contact_phone="5593999887766",
        channel="whatsapp",
        status="open",
        ai_enabled=True,
    )
    lead = Lead(
        company_id=company.id,
        phone="5593999887766",
        cpf="52998224725",
        unit="Santarém - 24 horas",
        is_student=True,
    )
    db_session.add_all([verify, schedule, conv, lead])
    await db_session.commit()

    tools_by_key = {verify.tool_key: verify, schedule.tool_key: schedule}
    reply, _ = await _run_physical_eval_pipeline(
        db_session,
        conv,
        "52998224725",
        tools_by_key,
        lead,
        config=config,
        is_first_contact=False,
        ai_name="Mônica",
        recent_customer_texts=["Quero agendar avaliação física"],
    )

    assert reply is not None
    assert "dia" in reply.lower() or "amanhã" in reply.lower()


@pytest.mark.asyncio
async def test_physical_eval_pipeline_calls_schedule_tool(db_session, company):
    from app.models import AiConfig, Conversation, Lead, Tool
    from app.security import encrypt_secret

    config = AiConfig(
        company_id=company.id,
        ai_name="Mônica",
        llm_api_key_encrypted=encrypt_secret("sk-test"),
    )
    db_session.add(config)
    await db_session.flush()
    verify = Tool(
        company_id=company.id,
        ai_config_id=config.id,
        name="Verificar unidade",
        tool_key=TOOL_KEY_VERIFY_UNIT_BY_CPF,
        description="Descobre unidade",
        parameters=[{"name": "cpf", "type": "string", "required": True}],
        webhook_url="https://example.com/verify",
        is_active=True,
    )
    schedule = Tool(
        company_id=company.id,
        ai_config_id=config.id,
        name="Consulta horários",
        tool_key=TOOL_KEY_CHECK_SCHEDULE,
        description="Consulta horários",
        parameters=[
            {"name": "unidade", "type": "string", "required": True},
            {"name": "data", "type": "string", "required": True},
        ],
        webhook_url="https://example.com/schedule",
        is_active=True,
    )
    conv = Conversation(
        company_id=company.id,
        contact_phone="5593999887766",
        channel="whatsapp",
        status="open",
        ai_enabled=True,
    )
    lead = Lead(
        company_id=company.id,
        phone="5593999887766",
        cpf="52998224725",
        unit="Santarém - 24 horas",
        is_student=True,
    )
    db_session.add_all([verify, schedule, conv, lead])
    await db_session.commit()

    tools_by_key = {verify.tool_key: verify, schedule.tool_key: schedule}
    schedule_payload = {
        "sucesso": True,
        "mensagem": "Horários disponíveis consultados.",
        "dados": {
            "unidade": "Santarém - 24 horas",
            "data": "20260915",
            "data_formatada": "15/09/2026",
            "horarios_disponiveis": ["09:00", "11:00"],
        },
    }

    with patch(
        "app.services.message_flow.execute_tool",
        new_callable=AsyncMock,
        return_value=schedule_payload,
    ) as execute_mock, patch(
        "app.services.message_flow._humanize_tool_reply_with_llm",
        new_callable=AsyncMock,
        return_value="No dia 15/09 tem 09:00 e 11:00 livres. Qual prefere?",
    ):
        reply, _ = await _run_physical_eval_pipeline(
            db_session,
            conv,
            "15/09/2026",
            tools_by_key,
            lead,
            config=config,
            is_first_contact=False,
            ai_name="Mônica",
            recent_customer_texts=["Quero agendar avaliação física"],
        )

    execute_mock.assert_awaited_once()
    assert execute_mock.await_args.args[2] == {
        "unidade": "Santarém - 24 horas",
        "data": "20260915",
    }
    assert reply == "No dia 15/09 tem 09:00 e 11:00 livres. Qual prefere?"


@pytest.mark.asyncio
async def test_physical_eval_pipeline_rejects_weekend(db_session, company):
    from app.models import AiConfig, Conversation, Lead, Tool
    from app.security import encrypt_secret

    config = AiConfig(
        company_id=company.id,
        ai_name="Mônica",
        llm_api_key_encrypted=encrypt_secret("sk-test"),
    )
    db_session.add(config)
    await db_session.flush()
    verify = Tool(
        company_id=company.id,
        ai_config_id=config.id,
        name="Verificar unidade",
        tool_key=TOOL_KEY_VERIFY_UNIT_BY_CPF,
        description="Descobre unidade",
        parameters=[{"name": "cpf", "type": "string", "required": True}],
        webhook_url="https://example.com/verify",
        is_active=True,
    )
    schedule = Tool(
        company_id=company.id,
        ai_config_id=config.id,
        name="Consulta horários",
        tool_key=TOOL_KEY_CHECK_SCHEDULE,
        description="Consulta horários",
        parameters=[
            {"name": "unidade", "type": "string", "required": True},
            {"name": "data", "type": "string", "required": True},
        ],
        webhook_url="https://example.com/schedule",
        is_active=True,
    )
    conv = Conversation(
        company_id=company.id,
        contact_phone="5593999887766",
        channel="whatsapp",
        status="open",
        ai_enabled=True,
    )
    lead = Lead(
        company_id=company.id,
        phone="5593999887766",
        cpf="52998224725",
        unit="Santarém - 24 horas",
        is_student=True,
    )
    db_session.add_all([verify, schedule, conv, lead])
    await db_session.commit()

    tools_by_key = {verify.tool_key: verify, schedule.tool_key: schedule}

    with patch(
        "app.services.message_flow.execute_tool",
        new_callable=AsyncMock,
    ) as execute_mock:
        reply, _ = await _run_physical_eval_pipeline(
            db_session,
            conv,
            "sábado",
            tools_by_key,
            lead,
            config=config,
            is_first_contact=False,
            ai_name="Mônica",
            recent_customer_texts=["Quero agendar avaliação física"],
        )

    execute_mock.assert_not_awaited()
    assert reply is not None
    assert "segunda" in reply.lower()
    assert "sexta" in reply.lower()
