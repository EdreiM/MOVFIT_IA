"""Relatório operacional de atendimentos da IA — Fase 1."""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AiConfig, Conversation, Lead, Message, ToolCallLog
from app.schemas import (
    AiOperationsReport,
    OperationsDailyVolume,
    OperationsMotivation,
    OperationsResponseTimes,
    OperationsSummary,
    OperationsTransferReason,
    OperationsUnitRow,
)
from app.services.message_flow import (
    TOOL_KEY_BOOK_PHYSICAL_EVAL,
    TOOL_KEY_END,
    TOOL_KEY_SEND_PLAN_IMAGES,
    TOOL_KEY_TRANSFER,
    TOOL_KEY_VERIFY_UNIT_BY_CPF,
    sanitize_phone_digits,
)

_BRAZIL_TZ = ZoneInfo("America/Sao_Paulo")
_BUSINESS_START = time(8, 0)
_BUSINESS_END = time(18, 0)


def _period_bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    start = datetime.combine(date_from, time.min, tzinfo=_BRAZIL_TZ)
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=_BRAZIL_TZ)
    return start, end


def _classify_conversation_motive(tool_keys: set[str], transfer_motivo: str | None) -> str:
    motivo = (transfer_motivo or "").lower()
    if TOOL_KEY_TRANSFER in tool_keys:
        if any(w in motivo for w in ("cancel", "cancelamento")):
            return "Solicitação de cancelamento"
        if "tour" in motivo:
            return "Tour pela academia"
        if "loja interna" in motivo or "estoque" in motivo:
            return "Loja interna (vestuário)"
        if motivo.strip():
            return "Transferido — outro motivo"
        return "Transferido"
    if TOOL_KEY_BOOK_PHYSICAL_EVAL in tool_keys:
        return "Avaliação física"
    if TOOL_KEY_SEND_PLAN_IMAGES in tool_keys:
        return "Dúvidas sobre planos"
    if TOOL_KEY_VERIFY_UNIT_BY_CPF in tool_keys:
        return "Identificação de aluno / CPF"
    if TOOL_KEY_END in tool_keys:
        return "Dúvida resolvida pela IA"
    if any("convidado" in k or "guest" in k for k in tool_keys):
        return "Convidados"
    if any("parcela" in k or "pagamento" in k or "boleto" in k for k in tool_keys):
        return "Financeiro"
    return "Geral / não classificado"


def _first_ai_response_seconds(messages: list[Message]) -> float | None:
    first_inbound: datetime | None = None
    for message in sorted(messages, key=lambda m: m.created_at):
        if message.actor == "customer" and message.direction == "inbound":
            if first_inbound is None:
                first_inbound = message.created_at
            continue
        if first_inbound and message.actor == "ai" and message.direction == "outbound":
            if message.created_at.tzinfo is None:
                created = message.created_at.replace(tzinfo=timezone.utc)
            else:
                created = message.created_at
            if first_inbound.tzinfo is None:
                inbound = first_inbound.replace(tzinfo=timezone.utc)
            else:
                inbound = first_inbound
            return max(0.0, (created - inbound).total_seconds())
    return None


def _is_business_hours(dt: datetime) -> bool:
    local = dt.astimezone(_BRAZIL_TZ)
    return local.weekday() < 5 and _BUSINESS_START <= local.time() < _BUSINESS_END


def _compute_response_stats(
    samples: list[float],
    business_samples: list[float],
) -> OperationsResponseTimes:
    if not samples:
        return OperationsResponseTimes(
            median_seconds=None,
            median_business_hours_seconds=None,
            within_30min_pct=None,
            over_4h_pct=None,
            samples=0,
        )
    within_30 = sum(1 for s in samples if s <= 1800)
    over_4h = sum(1 for s in samples if s > 14400)
    return OperationsResponseTimes(
        median_seconds=statistics.median(samples),
        median_business_hours_seconds=(
            statistics.median(business_samples) if business_samples else None
        ),
        within_30min_pct=within_30 / len(samples),
        over_4h_pct=over_4h / len(samples),
        samples=len(samples),
    )


async def compute_ai_operations_report(
    db: AsyncSession,
    company_id: UUID,
    *,
    date_from: date,
    date_to: date,
) -> AiOperationsReport:
    start, end = _period_bounds(date_from, date_to)

    config_result = await db.execute(
        select(AiConfig.ai_name).where(
            AiConfig.company_id == company_id,
            AiConfig.integration_id.is_(None),
        )
    )
    ai_name = config_result.scalar() or "Mônica"

    conv_result = await db.execute(
        select(Conversation).where(
            Conversation.company_id == company_id,
            Conversation.channel != "test_console",
            Conversation.created_at >= start,
            Conversation.created_at < end,
        )
    )
    conversations = conv_result.scalars().all()
    conv_ids = [c.id for c in conversations]
    phones = {sanitize_phone_digits(c.contact_phone) for c in conversations if c.contact_phone}

    messages_inbound = 0
    messages_outbound = 0
    if conv_ids:
        msg_counts = await db.execute(
            select(Message.direction, func.count())
            .where(Message.conversation_id.in_(conv_ids))
            .group_by(Message.direction)
        )
        for direction, count in msg_counts.all():
            if direction == "inbound":
                messages_inbound = count
            elif direction == "outbound":
                messages_outbound = count

    ai_resolved = sum(1 for c in conversations if c.status == "resolved" and c.ai_enabled)
    with_human = sum(1 for c in conversations if c.status == "with_human")

    tool_logs: dict[UUID, list[ToolCallLog]] = defaultdict(list)
    if conv_ids:
        logs_result = await db.execute(
            select(ToolCallLog).where(
                ToolCallLog.company_id == company_id,
                ToolCallLog.conversation_id.in_(conv_ids),
                ToolCallLog.success.is_(True),
            )
        )
        for log in logs_result.scalars().all():
            tool_logs[log.conversation_id].append(log)

    leads_by_phone: dict[str, Lead] = {}
    if phones:
        leads_result = await db.execute(
            select(Lead).where(Lead.company_id == company_id, Lead.phone.in_(list(phones)))
        )
        for lead in leads_result.scalars().all():
            leads_by_phone[lead.phone] = lead

    messages_by_conv: dict[UUID, list[Message]] = defaultdict(list)
    if conv_ids:
        all_messages = await db.execute(
            select(Message)
            .where(Message.conversation_id.in_(conv_ids))
            .order_by(Message.created_at.asc())
        )
        for message in all_messages.scalars().all():
            messages_by_conv[message.conversation_id].append(message)

    abandoned = 0
    response_samples: list[float] = []
    business_response_samples: list[float] = []
    motive_counter: Counter[str] = Counter()
    unit_stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"conversations": 0, "plans": 0, "transfers": 0, "cancellations": 0}
    )
    transfer_reason_counter: Counter[str] = Counter()
    daily_conv: Counter[str] = Counter()
    daily_inbound: Counter[str] = Counter()
    plans_presented = 0
    physical_evals = 0
    cancellations = 0

    for conv in conversations:
        day_key = conv.created_at.astimezone(_BRAZIL_TZ).date().isoformat()
        daily_conv[day_key] += 1

        logs = tool_logs.get(conv.id, [])
        tool_keys = {log.tool_key for log in logs}
        transfer_motivo = None
        for log in logs:
            if log.tool_key == TOOL_KEY_TRANSFER:
                transfer_motivo = str((log.arguments or {}).get("motivo") or "")
                reason = transfer_motivo.strip() or "Sem motivo informado"
                transfer_reason_counter[reason[:200]] += 1

        motive = _classify_conversation_motive(tool_keys, transfer_motivo)
        motive_counter[motive] += 1

        if TOOL_KEY_SEND_PLAN_IMAGES in tool_keys:
            plans_presented += 1
        if TOOL_KEY_BOOK_PHYSICAL_EVAL in tool_keys:
            physical_evals += 1
        if TOOL_KEY_TRANSFER in tool_keys and transfer_motivo and "cancel" in transfer_motivo.lower():
            cancellations += 1

        phone = sanitize_phone_digits(conv.contact_phone)
        lead = leads_by_phone.get(phone)
        unit_label = (lead.unit if lead and lead.unit else "Sem unidade definida").strip()
        unit_stats[unit_label]["conversations"] += 1
        if TOOL_KEY_SEND_PLAN_IMAGES in tool_keys:
            unit_stats[unit_label]["plans"] += 1
        if TOOL_KEY_TRANSFER in tool_keys:
            unit_stats[unit_label]["transfers"] += 1
        if lead and lead.wants_cancellation:
            unit_stats[unit_label]["cancellations"] += 1

        messages = messages_by_conv.get(conv.id, [])
        for message in messages:
            if message.direction == "inbound":
                d = message.created_at.astimezone(_BRAZIL_TZ).date().isoformat()
                daily_inbound[d] += 1

        if messages:
            last = messages[-1]
            if (
                last.actor == "customer"
                and conv.status == "open"
                and conv.ai_enabled
            ):
                abandoned += 1

        seconds = _first_ai_response_seconds(messages)
        if seconds is not None:
            response_samples.append(seconds)
            first_inbound = next(
                (m for m in messages if m.actor == "customer" and m.direction == "inbound"),
                None,
            )
            if first_inbound and _is_business_hours(first_inbound.created_at):
                business_response_samples.append(seconds)

    transferred = sum(
        1
        for c in conversations
        if any(log.tool_key == TOOL_KEY_TRANSFER for log in tool_logs.get(c.id, []))
    )

    days_span = max(1, (date_to - date_from).days + 1)
    outcomes = ai_resolved + with_human + transferred
    rate = (ai_resolved / outcomes) if outcomes > 0 else None

    motivations = []
    total_classified = sum(motive_counter.values()) or 1
    for label, count in motive_counter.most_common():
        motivations.append(
            OperationsMotivation(label=label, count=count, pct=count / total_classified)
        )

    daily_volume = []
    cursor = date_from
    while cursor <= date_to:
        key = cursor.isoformat()
        daily_volume.append(
            OperationsDailyVolume(
                day=key,
                conversations=daily_conv.get(key, 0),
                inbound_messages=daily_inbound.get(key, 0),
            )
        )
        cursor += timedelta(days=1)

    units = [
        OperationsUnitRow(
            unit=unit,
            conversations=stats["conversations"],
            plans=stats["plans"],
            transfers=stats["transfers"],
            cancellations=stats["cancellations"],
        )
        for unit, stats in sorted(unit_stats.items(), key=lambda x: -x[1]["conversations"])
    ]

    transfer_reasons = [
        OperationsTransferReason(reason=reason, count=count)
        for reason, count in transfer_reason_counter.most_common(15)
    ]

    response_times = _compute_response_stats(response_samples, business_response_samples)

    return AiOperationsReport(
        period_from=date_from,
        period_to=date_to,
        generated_at=datetime.now(timezone.utc),
        ai_name=ai_name,
        summary=OperationsSummary(
            conversations_total=len(conversations),
            unique_contacts=len(phones),
            messages_inbound=messages_inbound,
            messages_outbound=messages_outbound,
            ai_resolved=ai_resolved,
            transferred=transferred,
            with_human=with_human,
            abandoned_by_client=abandoned,
            ai_resolution_rate=rate,
            plans_presented=plans_presented,
            physical_evals_scheduled=physical_evals,
            cancellation_requests=cancellations,
            avg_conversations_per_day=len(conversations) / days_span,
        ),
        daily_volume=daily_volume,
        motivations=motivations,
        units=units,
        transfer_reasons=transfer_reasons,
        response_times=response_times,
    )
