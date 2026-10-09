"""Avaliação física/bioimpedância é só pra aluno: quem diz que não é aluno
não entra no fluxo de agendamento (que pediria o CPF)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.models import AiConfig, Conversation, Lead, Message, Tool
from app.security import encrypt_secret
from app.services.message_flow import (
    TOOL_KEY_CHECK_SCHEDULE,
    TOOL_KEY_VERIFY_UNIT_BY_CPF,
    _declared_not_student,
    _says_not_student,
    generate_ai_reply,
    sanitize_phone_digits,
)

NOT_STUDENT_QUESTION = (
    "Gostaria de saber se vocês realizam a avaliação bioimpedância para alunos não são matriculados ?"
)


def test_says_not_student():
    assert _says_not_student(NOT_STUDENT_QUESTION) is True
    assert _says_not_student("ainda não sou aluno, dá pra fazer a avaliação?") is True
    assert _says_not_student("quero fazer bioimpedância sem ser aluna") is True
    assert _says_not_student("sou aluno e quero agendar avaliação física") is False
    assert _says_not_student("não consigo ir amanhã") is False


def test_declared_not_student_uses_the_most_recent_statement():
    assert _declared_not_student("Sim", [NOT_STUDENT_QUESTION, "Qual o valor da avaliação?"]) is True
    # Se depois ele diz que é aluno, ou manda o CPF, a consulta do CPF é quem decide.
    assert _declared_not_student("na verdade já sou aluno", [NOT_STUDENT_QUESTION]) is False
    assert _declared_not_student("52998224725", [NOT_STUDENT_QUESTION]) is False
    assert _declared_not_student("Quero agendar avaliação física", []) is False


async def _setup(db_session, company, *, is_student: bool = False) -> Conversation:
    config = AiConfig(company_id=company.id, ai_name="Mônica", llm_api_key_encrypted=encrypt_secret("sk-test"))
    db_session.add(config)
    await db_session.flush()
    for name, key in (("Verificar unidade", TOOL_KEY_VERIFY_UNIT_BY_CPF), ("Horários", TOOL_KEY_CHECK_SCHEDULE)):
        db_session.add(
            Tool(
                company_id=company.id,
                ai_config_id=config.id,
                name=name,
                tool_key=key,
                description=name,
                parameters=[{"name": "cpf", "type": "string", "required": True}],
                webhook_url="https://example.com/hook",
                is_active=True,
            )
        )
    conv = Conversation(
        company_id=company.id,
        contact_phone="5593999887722",
        channel="test_console",
        status="open",
        ai_enabled=True,
    )
    db_session.add(conv)
    if is_student:
        db_session.add(
            Lead(
                company_id=company.id,
                phone=sanitize_phone_digits(conv.contact_phone),
                stage="aluno",
                is_student=True,
                unit="Itaituba",
                cpf="52998224725",
            )
        )
    await db_session.commit()
    return conv


async def _add_history(db_session, conv, *turns: tuple[str, str]) -> None:
    now = datetime.now(timezone.utc)
    for i, (actor, text) in enumerate(turns):
        db_session.add(
            Message(
                company_id=conv.company_id,
                conversation_id=conv.id,
                direction="inbound" if actor == "customer" else "outbound",
                actor=actor,
                content_type="text",
                text=text,
                created_at=now - timedelta(minutes=len(turns) - i),
            )
        )
    await db_session.commit()


async def _reply(db_session, conv, user_text: str, llm_text: str = "Claro!"):
    with patch(
        "app.services.message_flow.fetch_rag_context", new_callable=AsyncMock, return_value=""
    ), patch(
        "app.services.message_flow.chat_completion",
        new_callable=AsyncMock,
        return_value={"content": llm_text, "tool_calls": None},
    ) as llm_mock, patch(
        "app.services.message_flow._run_physical_eval_pipeline", new_callable=AsyncMock
    ) as pipeline_mock:
        pipeline_mock.return_value = ("Para agendar sua *avaliação física*, preciso do seu *CPF*.", None)
        reply, _, _ = await generate_ai_reply(db_session, conv, user_text)
    return reply, llm_mock, pipeline_mock


@pytest.mark.asyncio
async def test_non_student_asking_about_bioimpedance_is_not_asked_for_cpf(db_session, company):
    """Regressão de produção: a pergunta abaixo recebia "Para agendar sua
    avaliação física, preciso do seu CPF"."""
    conv = await _setup(db_session, company)
    await _add_history(db_session, conv, ("customer", "Olá bom dia!!"), ("ai", "Olá! Aqui é a Mônica."))

    reply, llm_mock, pipeline_mock = await _reply(db_session, conv, NOT_STUDENT_QUESTION)

    pipeline_mock.assert_not_awaited()
    llm_mock.assert_not_awaited()  # resposta fixa: regra do negócio não depende do modelo
    assert "exclusivas pra quem já é aluno" in reply
    assert "CPF" not in reply
    assert "planos" in reply


@pytest.mark.asyncio
async def test_non_student_followups_stay_out_of_the_scheduling_flow(db_session, company):
    conv = await _setup(db_session, company)
    await _add_history(
        db_session,
        conv,
        ("customer", NOT_STUDENT_QUESTION),
        ("ai", "A avaliação física e a bioimpedância são exclusivas pra quem já é aluno da Mov Fit."),
    )

    # "Sim" e pergunta de valor caíam de volta no fluxo de agendamento.
    for follow_up in ("Qual o valor da avaliação?", "Sim"):
        reply, llm_mock, pipeline_mock = await _reply(db_session, conv, follow_up)
        pipeline_mock.assert_not_awaited()
        system = "\n".join(
            m["content"] for m in llm_mock.await_args_list[0].kwargs["messages"] if m["role"] == "system"
        )
        assert "o cliente disse que NÃO é aluno" in system
        assert reply == "Claro!"


@pytest.mark.asyncio
async def test_student_still_goes_through_the_scheduling_flow(db_session, company):
    conv = await _setup(db_session, company, is_student=True)

    reply, _, pipeline_mock = await _reply(db_session, conv, "Quero agendar minha avaliação física")

    pipeline_mock.assert_awaited_once()
    assert "CPF" in reply


@pytest.mark.asyncio
async def test_unknown_customer_asking_to_schedule_still_gets_the_cpf_check(db_session, company):
    conv = await _setup(db_session, company)

    _, _, pipeline_mock = await _reply(db_session, conv, "Quero agendar minha avaliação física")

    # Sem declaração nenhuma, quem confirma se é aluno é a consulta do CPF.
    pipeline_mock.assert_awaited_once()
