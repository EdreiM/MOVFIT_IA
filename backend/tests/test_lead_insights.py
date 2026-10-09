"""Etiquetas automáticas, funil de vendas, resumo de transferência e
perguntas sem resposta (app/services/lead_insights.py)."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models import AiConfig, Conversation, KnowledgeGap, Lead, Message, Plan, Tool, Unit
from app.security import encrypt_secret
from app.services.lead_insights import (
    advance_sales_stage,
    compute_sales_insights,
    detect_knowledge_gap,
    detect_tags,
    mark_lead_lost,
    merge_tags,
)
from app.services.message_flow import (
    TOOL_KEY_TRANSFER,
    execute_tool,
    reply_to_pending_messages,
    sanitize_phone_digits,
)

UNIT_NAME = "Santarém - 24 horas"
SIGNUP_URL = "https://exemplo.com/matricula/anual"
PHONE = "5593999887733"


def test_detect_tags_topics_and_objections():
    assert detect_tags("quero ver os planos") == {"assunto:planos"}
    assert detect_tags("quanto custa a academia?") == {"assunto:planos"}
    assert "objecao:preco" in detect_tags("hmm achei meio salgado")
    assert "objecao:vou_pensar" in detect_tags("vou ver com minha esposa e te falo")
    assert "objecao:fidelidade" in detect_tags("não quero fidelidade")
    assert detect_tags("meu boleto está atrasado") == {"assunto:financeiro"}
    assert "assunto:cancelamento" in detect_tags("quero cancelar minha matrícula")
    assert "assunto:avaliacao_fisica" in detect_tags("quero agendar bioimpedância")
    assert detect_tags("qual o horário de funcionamento?") == {"assunto:informacoes"}
    # "cara" como vocativo não é objeção de preço; cumprimento não gera etiqueta.
    assert "objecao:preco" not in detect_tags("cara, que horas abre?")
    assert detect_tags("boa tarde") == set()


def test_merge_tags_replaces_single_value_categories():
    merged = merge_tags(
        ["assunto:planos", "unidade:Itaituba", "plano:Mensal"],
        {"unidade:Santarém - 24 horas", "objecao:preco", "assunto:planos"},
    )
    assert merged == ["assunto:planos", "plano:Mensal", "objecao:preco", "unidade:Santarém - 24 horas"]


def test_sales_stage_only_moves_forward():
    assert advance_sales_stage(None, interested=True, qualified=False, proposal=False) == "interessado"
    assert advance_sales_stage("interessado", interested=True, qualified=True, proposal=False) == "qualificado"
    assert advance_sales_stage("qualificado", interested=True, qualified=True, proposal=True) == "proposta"
    # Não volta, e sem sinal novo não muda.
    assert advance_sales_stage("proposta", interested=True, qualified=False, proposal=False) == "proposta"
    assert advance_sales_stage("qualificado", interested=False, qualified=False, proposal=False) == "qualificado"
    # Matriculado é definitivo; perdido reativa com sinal novo.
    assert advance_sales_stage("matriculado", interested=True, qualified=True, proposal=True) == "matriculado"
    assert advance_sales_stage("perdido", interested=True, qualified=False, proposal=False) == "interessado"
    assert advance_sales_stage("perdido", interested=False, qualified=False, proposal=False) == "perdido"


def test_detect_knowledge_gap():
    assert detect_knowledge_gap("Sobre o número da unidade, não tenho essa informação aqui na base.") == "sem_informacao"
    assert (
        detect_knowledge_gap("Aqui comigo eu tenho os planos que te passei. Sobre diária, prefiro confirmar com a equipe.")
        == "modalidade_fora_do_catalogo"
    )
    assert detect_knowledge_gap("O plano anual sai por R$ 197,00 por mês.") is None


@pytest_asyncio.fixture
async def sales_conversation(db_session, company):
    unit = Unit(company_id=company.id, name=UNIT_NAME, city="Santarém", unit_type="Premium")
    db_session.add(unit)
    await db_session.flush()
    db_session.add_all(
        [
            Plan(
                company_id=company.id, unit_id=unit.id, name="Plano Anual Parcelado",
                monthly_price=197.0, fidelity_months=12, signup_url=SIGNUP_URL,
            ),
            Plan(company_id=company.id, unit_id=unit.id, name="Plano Mensal Recorrente", monthly_price=217.0),
        ]
    )
    config = AiConfig(
        company_id=company.id, ai_name="Mônica", llm_api_key_encrypted=encrypt_secret("sk-test"),
        sales_mode_enabled=True,
    )
    conv = Conversation(
        company_id=company.id, contact_phone=PHONE, contact_name="Edrei",
        channel="whatsapp", status="open", ai_enabled=True,
    )
    db_session.add_all([config, conv])
    await db_session.commit()
    return config, conv


async def _turn(db_session, conv, customer_text: str, llm_reply: str) -> str | None:
    db_session.add(
        Message(
            company_id=conv.company_id, conversation_id=conv.id, direction="inbound", actor="customer",
            content_type="text", text=customer_text, created_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()
    with patch(
        "app.services.message_flow.fetch_rag_context", new_callable=AsyncMock, return_value=""
    ), patch(
        "app.services.message_flow.chat_completion",
        new_callable=AsyncMock,
        return_value={"content": llm_reply, "tool_calls": None},
    ), patch("app.services.message_flow.send_outbound", new_callable=AsyncMock), patch(
        "app.services.message_flow.asyncio.sleep", new_callable=AsyncMock
    ):
        reply = await reply_to_pending_messages(db_session, conv.id, conv.company_id)
        await db_session.commit()
    return reply


async def _lead(db_session, conv) -> Lead | None:
    result = await db_session.execute(
        select(Lead).where(
            Lead.company_id == conv.company_id, Lead.phone == sanitize_phone_digits(conv.contact_phone)
        )
    )
    lead = result.scalar_one_or_none()
    if lead:
        await db_session.refresh(lead)
    return lead


@pytest.mark.asyncio
async def test_conversation_tags_the_lead_and_moves_the_sales_funnel(db_session, sales_conversation):
    _, conv = sales_conversation

    await _turn(db_session, conv, "oi", "Olá! Como posso ajudar?")
    assert await _lead(db_session, conv) is None  # cumprimento não vira cadastro

    await _turn(
        db_session, conv, f"quero ver os planos de {UNIT_NAME}",
        f"Na {UNIT_NAME} o Plano Anual Parcelado sai por R$ 197,00 por mês.\n\nPosso te mandar o link?",
    )
    lead = await _lead(db_session, conv)
    assert lead.sales_stage == "qualificado"
    assert {"assunto:planos", f"unidade:{UNIT_NAME}"} <= set(lead.tags)

    await _turn(db_session, conv, "achei caro", "Dá menos de R$ 7 por dia. Quer que eu mande o link?")
    lead = await _lead(db_session, conv)
    assert "objecao:preco" in lead.tags
    assert lead.sales_stage == "qualificado"

    await _turn(db_session, conv, "tá, quero o anual", f"Perfeito! É só completar o cadastro: {SIGNUP_URL}")
    lead = await _lead(db_session, conv)
    assert lead.sales_stage == "proposta"
    assert "plano:Plano Anual Parcelado" in lead.tags


@pytest.mark.asyncio
async def test_student_is_tagged_but_stays_out_of_the_sales_funnel(db_session, company, sales_conversation):
    _, conv = sales_conversation
    db_session.add(
        Lead(company_id=company.id, phone=PHONE, stage="aluno", is_student=True, unit="Itaituba")
    )
    await db_session.commit()

    await _turn(db_session, conv, "meu boleto está atrasado", "Vou verificar pra você.")

    lead = await _lead(db_session, conv)
    assert {"assunto:financeiro", "perfil:aluno", "unidade:Itaituba"} <= set(lead.tags)
    assert lead.sales_stage is None


@pytest.mark.asyncio
async def test_unanswered_question_is_logged(db_session, sales_conversation):
    _, conv = sales_conversation
    question = "qual o telefone da unidade express?"

    await _turn(db_session, conv, question, "Sobre esse número, não tenho essa informação aqui na base.")

    gaps = (await db_session.execute(select(KnowledgeGap))).scalars().all()
    assert [(g.question, g.reason) for g in gaps] == [(question, "sem_informacao")]


@pytest.mark.asyncio
async def test_failure_in_insights_never_blocks_the_reply(db_session, sales_conversation):
    _, conv = sales_conversation
    with patch(
        "app.services.message_flow.update_lead_insights", new_callable=AsyncMock, side_effect=RuntimeError("boom")
    ):
        reply = await _turn(db_session, conv, "quero ver os planos", "Claro! De qual unidade?")

    assert reply == "Claro! De qual unidade?"
    sent = (
        await db_session.execute(
            select(Message).where(Message.conversation_id == conv.id, Message.actor == "ai")
        )
    ).scalars().all()
    assert [m.text for m in sent] == ["Claro! De qual unidade?"]


@pytest.mark.asyncio
async def test_lost_only_for_leads_that_never_got_a_proposal(db_session, company, sales_conversation):
    _, conv = sales_conversation
    lead = Lead(
        company_id=company.id, phone=PHONE, stage="novo", sales_stage="qualificado",
        tags=["assunto:planos", "objecao:preco"],
    )
    db_session.add(lead)
    await db_session.commit()

    await mark_lead_lost(db_session, conv)
    assert (lead.sales_stage, lead.lost_reason) == ("perdido", "preco")

    # Quem recebeu o link pode ter se matriculado sem responder — não vira perdido.
    lead.sales_stage, lead.lost_reason = "proposta", None
    await db_session.flush()
    await mark_lead_lost(db_session, conv)
    assert lead.sales_stage == "proposta"


@pytest.mark.asyncio
async def test_transfer_sends_and_stores_a_summary_for_the_attendant(db_session, company, sales_conversation):
    config, conv = sales_conversation
    tool = Tool(
        company_id=company.id, ai_config_id=config.id, name="Transferir atendimento",
        tool_key=TOOL_KEY_TRANSFER, description="Transfere",
        parameters=[{"name": "motivo", "type": "string", "required": True}],
        webhook_url="https://example.com/transferir", is_active=True,
    )
    lead = Lead(
        company_id=company.id, phone=PHONE, name="Edrei Maciel", stage="novo", sales_stage="qualificado",
        tags=["assunto:planos", f"unidade:{UNIT_NAME}", "plano:Plano Anual Parcelado", "objecao:preco"],
    )
    db_session.add_all(
        [
            tool,
            lead,
            Message(
                company_id=company.id, conversation_id=conv.id, direction="inbound", actor="customer",
                content_type="text", text="quero conhecer a academia antes", created_at=datetime.now(timezone.utc),
            ),
        ]
    )
    await db_session.commit()

    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {"sucesso": True, "mensagem": "Transferido.", "dados": {}}
    with patch("app.services.message_flow.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post = AsyncMock(return_value=response)
        client_cls.return_value = client
        await execute_tool(db_session, tool, {"motivo": "Cliente quer agendar tour"}, conv)
        await db_session.commit()

    summary = client.post.await_args.kwargs["json"]["contexto"]["resumo_atendimento"]
    assert conv.handoff_summary == summary
    for expected in (
        "Cliente: Edrei Maciel",
        "Assunto: planos, tour",
        f"unidade {UNIT_NAME}",
        "plano Plano Anual Parcelado",
        "Objeções: preço",
        "Motivo da transferência: Cliente quer agendar tour",
        "“quero conhecer a academia antes”",
    ):
        assert expected in summary
    await db_session.refresh(lead)
    assert lead.sales_stage == "proposta"  # passou pra um atendente fechar


@pytest.mark.asyncio
async def test_sales_insights_counts_funnel_and_tags(db_session, company):
    db_session.add_all(
        [
            Lead(company_id=company.id, phone="1", sales_stage="interessado", tags=["assunto:planos"]),
            Lead(
                company_id=company.id, phone="2", sales_stage="perdido", lost_reason="preco",
                tags=["assunto:planos", "objecao:preco", "unidade:Itaituba"],
            ),
            Lead(company_id=company.id, phone="3", sales_stage="perdido", lost_reason="sem_resposta", tags=[]),
            Lead(company_id=company.id, phone="4", tags=["assunto:financeiro", "perfil:aluno"], is_student=True),
            Lead(company_id=company.id, phone="5"),  # sem etiqueta nem funil: fica fora
        ]
    )
    await db_session.commit()

    insights = await compute_sales_insights(db_session, company.id)

    stages = {s["stage"]: s["count"] for s in insights["stages"]}
    assert stages == {"interessado": 1, "qualificado": 0, "proposta": 0, "matriculado": 0, "perdido": 2}
    assert insights["leads_in_funnel"] == 3
    assert {(r["value"], r["label"], r["count"]) for r in insights["lost_reasons"]} == {
        ("preco", "preço", 1),
        ("sem_resposta", "parou de responder", 1),
    }
    assert {(t["value"], t["count"]) for t in insights["topics"]} == {("planos", 2), ("financeiro", 1)}
    assert insights["objections"] == [{"value": "preco", "label": "preço", "count": 1}]
    assert insights["units"] == [{"value": "Itaituba", "label": "Itaituba", "count": 1}]
