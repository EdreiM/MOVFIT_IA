"""Relatório narrativo das métricas — primeira pessoa, com base em dados reais."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AiConfig, Conversation, Lead, MetricsDaily, ToolCallLog
from app.schemas import MetricsOverview, MetricsNarrativeReport, StageCount, ToolStats

_TOOL_KEY_SEND_PLANS = "enviar_imagens_planos"
_TOOL_KEY_TRANSFER = "transferir_atendimento"
_TOOL_KEY_BOOK_EVAL = "insere_agenda_avalicao"
_TOOL_KEY_CHECK_SCHEDULE = "consultar_agendamento_horarios"
_TOOL_KEY_VERIFY_CPF = "verificar_unidade_por_cpf"


@dataclass
class NarrativeContext:
    ai_name: str
    overview: MetricsOverview
    open_conversations: int
    with_human_conversations: int
    leads_total: int
    messages_inbound_last_7_days: int
    messages_outbound_last_7_days: int
    funnel: list[StageCount]
    tool_stats: list[ToolStats]
    generated_at: datetime


def _tool_success(tool_stats: list[ToolStats], tool_key: str) -> int:
    for stat in tool_stats:
        if stat.tool_key == tool_key:
            return stat.success_calls
    return 0


def _stage_count(funnel: list[StageCount], stage: str) -> int:
    for entry in funnel:
        if entry.stage == stage:
            return entry.count
    return 0


def _pct(rate: float | None) -> str | None:
    if rate is None:
        return None
    return f"{round(rate * 100)}%"


def build_metrics_narrative(ctx: NarrativeContext) -> list[str]:
    """Monta parágrafos em primeira pessoa a partir dos dados coletados."""
    name = ctx.ai_name or "Mônica"
    o = ctx.overview
    paragraphs: list[str] = []

    if o.conversations_total == 0 and o.messages_inbound == 0:
        return [
            f"Oi! Aqui é a {name}. 😊",
            "Ainda não recebi nenhum atendimento real por aqui — assim que as conversas "
            "começarem, volto com um resumo de como estou ajudando clientes, agendamentos "
            "e tudo mais.",
        ]

    intro = f"Oi! Aqui é a {name} com um resumo de como estão indo os atendimentos. 😊"
    volume_bits: list[str] = []
    if o.conversations_total:
        conv_label = "conversa" if o.conversations_total == 1 else "conversas"
        volume_bits.append(f"já cuidei de {o.conversations_total} {conv_label}")
    if o.messages_inbound or o.messages_outbound:
        volume_bits.append(
            f"trocamos {o.messages_inbound} mensagens recebidas e {o.messages_outbound} enviadas"
        )
    if ctx.messages_inbound_last_7_days:
        volume_bits.append(
            f"nos últimos 7 dias recebi {ctx.messages_inbound_last_7_days} mensagens"
        )
    paragraphs.append(intro)
    paragraphs.append(
        "Até agora, "
        + ", ".join(volume_bits)
        + "."
    )

    status_bits: list[str] = []
    if ctx.open_conversations:
        label = "conversa aberta" if ctx.open_conversations == 1 else "conversas abertas"
        status_bits.append(f"tenho {ctx.open_conversations} {label} comigo agora")
    if ctx.with_human_conversations:
        label = "cliente" if ctx.with_human_conversations == 1 else "clientes"
        status_bits.append(
            f"{ctx.with_human_conversations} {label} aguardando um atendente humano"
        )
    rate_text = _pct(o.ai_resolution_rate)
    if rate_text and (o.ai_resolved + o.human_resolved) > 0:
        status_bits.append(
            f"resolvi sozinha {rate_text} dos atendimentos que chegaram ao fim "
            f"({o.ai_resolved} pela IA e {o.human_resolved} com apoio humano)"
        )
    elif o.ai_resolved or o.human_resolved:
        status_bits.append(
            f"já encerrei {o.ai_resolved + o.human_resolved} atendimentos "
            f"({o.ai_resolved} pela IA, {o.human_resolved} com humano)"
        )
    if status_bits:
        paragraphs.append("No momento, " + ", ".join(status_bits) + ".")

    outcomes: list[str] = []
    if o.students_total:
        label = "aluno" if o.students_total == 1 else "alunos"
        outcomes.append(f"identifiquei {o.students_total} {label} já matriculados")
    plan_sends = _tool_success(ctx.tool_stats, _TOOL_KEY_SEND_PLANS)
    if plan_sends:
        label = "conversa" if plan_sends == 1 else "conversas"
        outcomes.append(f"enviei material de planos em {plan_sends} {label}")
    if o.physical_evals_scheduled_total:
        label = "avaliação física" if o.physical_evals_scheduled_total == 1 else "avaliações físicas"
        outcomes.append(f"confirmei {o.physical_evals_scheduled_total} {label} agendadas")
    eval_bookings = _tool_success(ctx.tool_stats, _TOOL_KEY_BOOK_EVAL)
    if eval_bookings and eval_bookings != o.physical_evals_scheduled_total:
        outcomes.append(f"registrei {eval_bookings} reservas de horário de avaliação")
    schedule_lookups = _tool_success(ctx.tool_stats, _TOOL_KEY_CHECK_SCHEDULE)
    if schedule_lookups:
        outcomes.append(f"consultei horários disponíveis {schedule_lookups} vezes")
    cpf_checks = _tool_success(ctx.tool_stats, _TOOL_KEY_VERIFY_CPF)
    if cpf_checks:
        outcomes.append(f"verifiquei CPF/unidade {cpf_checks} vezes")
    if o.transferred_total:
        label = "pessoa" if o.transferred_total == 1 else "pessoas"
        outcomes.append(f"transferi {o.transferred_total} {label} pro time quando precisei de reforço")
    if outcomes:
        paragraphs.append("Alguns destaques: " + ", ".join(outcomes) + ".")

    if o.cancellation_requests_total:
        label = "cliente pediu" if o.cancellation_requests_total == 1 else "clientes pediram"
        paragraphs.append(
            f"Atenção: {o.cancellation_requests_total} {label} informações sobre cancelamento — "
            "vale acompanhar de perto."
        )

    funnel_bits: list[str] = []
    interessados = _stage_count(ctx.funnel, "interessado")
    qualificados = _stage_count(ctx.funnel, "qualificado")
    novos = _stage_count(ctx.funnel, "novo")
    if interessados:
        funnel_bits.append(f"{interessados} interessados")
    if qualificados:
        funnel_bits.append(f"{qualificados} qualificados")
    if novos and not interessados and not qualificados:
        funnel_bits.append(f"{novos} novos no funil")
    if ctx.leads_total and funnel_bits:
        paragraphs.append(
            f"No cadastro de clientes, tenho {ctx.leads_total} pessoas — "
            + ", ".join(funnel_bits)
            + ". Vamos converter! 💪"
        )
    elif ctx.leads_total:
        paragraphs.append(f"Já cadastrei {ctx.leads_total} clientes no sistema.")

    if len(paragraphs) <= 2 and o.conversations_total > 0:
        paragraphs.append(
            "O volume ainda está começando, mas estou acompanhando cada conversa de perto."
        )

    paragraphs.append("É isso por agora — sigo por aqui quando precisarem! ✨")
    return paragraphs


async def _recent_message_totals(
    db: AsyncSession, company_id: UUID, *, days: int = 7
) -> tuple[int, int]:
    since = date.today() - timedelta(days=days - 1)
    result = await db.execute(
        select(
            func.coalesce(func.sum(MetricsDaily.messages_inbound), 0),
            func.coalesce(func.sum(MetricsDaily.messages_outbound), 0),
        ).where(MetricsDaily.company_id == company_id, MetricsDaily.day >= since)
    )
    inbound, outbound = result.one()
    return int(inbound or 0), int(outbound or 0)


async def compute_metrics_narrative(db: AsyncSession, company_id: UUID) -> MetricsNarrativeReport:
    from app.routers.metrics import (
        _compute_leads_funnel,
        _compute_overview,
        _compute_tools_stats,
    )

    overview = await _compute_overview(db, company_id)
    funnel = await _compute_leads_funnel(db, company_id)
    tool_stats = await _compute_tools_stats(db, company_id)

    open_conversations = await db.scalar(
        select(func.count())
        .select_from(Conversation)
        .where(
            Conversation.company_id == company_id,
            Conversation.status == "open",
            Conversation.channel != "test_console",
        )
    )
    with_human_conversations = await db.scalar(
        select(func.count())
        .select_from(Conversation)
        .where(
            Conversation.company_id == company_id,
            Conversation.status == "with_human",
            Conversation.channel != "test_console",
        )
    )
    leads_total = await db.scalar(
        select(func.count()).select_from(Lead).where(Lead.company_id == company_id)
    )
    inbound_7d, outbound_7d = await _recent_message_totals(db, company_id)

    config_result = await db.execute(
        select(AiConfig.ai_name).where(
            AiConfig.company_id == company_id,
            AiConfig.integration_id.is_(None),
        )
    )
    ai_name = config_result.scalar() or "Mônica"
    generated_at = datetime.now(timezone.utc)

    ctx = NarrativeContext(
        ai_name=ai_name,
        overview=overview,
        open_conversations=open_conversations or 0,
        with_human_conversations=with_human_conversations or 0,
        leads_total=leads_total or 0,
        messages_inbound_last_7_days=inbound_7d,
        messages_outbound_last_7_days=outbound_7d,
        funnel=funnel,
        tool_stats=tool_stats,
        generated_at=generated_at,
    )
    paragraphs = build_metrics_narrative(ctx)
    return MetricsNarrativeReport(
        ai_name=ai_name,
        paragraphs=paragraphs,
        generated_at=generated_at,
    )
