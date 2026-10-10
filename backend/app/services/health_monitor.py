"""Alertas de saúde da operação e relatório diário de erros da IA.

Roda dentro da varredura periódica de follow-up (`run_followup_sweep`), que
já segura o advisory lock — não ganha namespace próprio porque nunca roda
fora dela. O que a IA faz de errado quase nunca dá erro visível: a ferramenta
do n8n cai, a transferência não chega em ninguém, o cliente fica sem
resposta — e só se descobria quando o cliente reclamava. Aqui isso vira
alerta no painel (e num webhook opcional, `ALERT_WEBHOOK_URL`)."""
import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

import httpx
from sqlalchemy import and_, case, exists, func, select

from app.config import get_settings
from app.models import (
    Company,
    Conversation,
    KnowledgeGap,
    Message,
    SystemEvent,
    ToolCallLog,
)
from app.services.message_flow import TOOL_KEY_CHECK_SESSION, TOOL_KEY_TRANSFER

logger = logging.getLogger(__name__)

_BRAZIL_TZ = timezone(timedelta(hours=-3))

TOOL_WINDOW_MINUTES = 30
TOOL_MIN_CALLS = 5
TOOL_FAILURE_RATE = 0.5
TRANSFER_PENDING_MINUTES = 10
UNANSWERED_MINUTES = 15
UNANSWERED_MAX_HOURS = 6  # mais velho que isso já virou abandono, não "fila"
UNANSWERED_MIN_CONVERSATIONS = 2

# Ferramentas que falham de propósito/ruído e não dizem nada sobre a saúde
# da operação (a checagem de sessão do WTS responde "erro" pra sessão antiga).
_IGNORED_TOOL_KEYS = {TOOL_KEY_CHECK_SESSION}
_TRANSFER_TOOL_KEY = TOOL_KEY_TRANSFER


@dataclass
class Condition:
    key: str
    severity: str
    title: str
    body: str


async def _notify(kind: str, severity: str, title: str, body: str, company_name: str) -> None:
    url = get_settings().alert_webhook_url.strip()
    if not url:
        return
    payload = {
        "tipo": "alerta" if kind == "alert" else "relatorio",
        "severidade": severity,
        "empresa": company_name,
        "titulo": title,
        "mensagem": body,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json=payload)
    except Exception:  # noqa: BLE001
        logger.warning("Falha ao enviar alerta pro webhook configurado", exc_info=True)


# ---------------------------------------------------------------- condições


async def _failing_tools(db, company_id, now: datetime) -> list[Condition]:
    since = now - timedelta(minutes=TOOL_WINDOW_MINUTES)
    failures = func.sum(case((ToolCallLog.success.is_(False), 1), else_=0))
    rows = (
        await db.execute(
            select(ToolCallLog.tool_key, func.max(ToolCallLog.tool_name), func.count(), failures)
            .where(
                ToolCallLog.company_id == company_id,
                ToolCallLog.created_at >= since,
                ToolCallLog.tool_key.not_in(_IGNORED_TOOL_KEYS),
            )
            .group_by(ToolCallLog.tool_key)
        )
    ).all()
    conditions: list[Condition] = []
    for tool_key, tool_name, total, failed in rows:
        failed = int(failed or 0)
        if total < TOOL_MIN_CALLS or failed / total < TOOL_FAILURE_RATE:
            continue
        last_error = await db.scalar(
            select(ToolCallLog.error_message)
            .where(
                ToolCallLog.company_id == company_id,
                ToolCallLog.tool_key == tool_key,
                ToolCallLog.success.is_(False),
                ToolCallLog.error_message.is_not(None),
            )
            .order_by(ToolCallLog.created_at.desc())
            .limit(1)
        )
        body = (
            f"A ferramenta \"{tool_name}\" falhou em {failed} de {total} chamadas "
            f"nos últimos {TOOL_WINDOW_MINUTES} minutos."
        )
        if last_error:
            body += f" Último erro: {last_error}"
        conditions.append(
            Condition(
                key=f"tool_failing:{tool_key}"[:120],
                severity="critical" if failed == total else "warning",
                title=f"Ferramenta com falha: {tool_name}",
                body=body,
            )
        )
    return conditions


async def _pending_transfers(db, company_id, now: datetime) -> list[Condition]:
    cutoff = now - timedelta(minutes=TRANSFER_PENDING_MINUTES)
    count = await db.scalar(
        select(func.count())
        .select_from(Conversation)
        .where(
            Conversation.company_id == company_id,
            Conversation.transfer_pending_at.is_not(None),
            Conversation.transfer_pending_at <= cutoff,
            Conversation.status != "with_human",
            Conversation.channel != "test_console",
        )
    )
    if not count:
        return []
    return [
        Condition(
            key="transfer_pending",
            severity="critical",
            title=f"{count} cliente(s) esperando transferência que não chegou a ninguém",
            body=(
                f"{count} conversa(s) tiveram a transferência pra atendente falhando há mais de "
                f"{TRANSFER_PENDING_MINUTES} minutos (a IA já tentou de novo em segundo plano). "
                "Veja em Conversas as marcadas com ⚠ e assuma manualmente; confira também o "
                "fluxo de transferência no n8n."
            ),
        )
    ]


def _unanswered_filter(company_id, now: datetime):
    """Conversas abertas, com a IA ligada, em que o cliente falou por último
    e ninguém (IA nem humano) respondeu depois."""
    newest = now - timedelta(minutes=UNANSWERED_MINUTES)
    oldest = now - timedelta(hours=UNANSWERED_MAX_HOURS)
    answered = exists().where(
        Message.conversation_id == Conversation.id,
        Message.actor.in_(("ai", "human_agent")),
        Message.created_at >= Conversation.last_message_at,
    )
    return and_(
        Conversation.company_id == company_id,
        Conversation.status == "open",
        Conversation.ai_enabled.is_(True),
        Conversation.channel != "test_console",
        Conversation.last_message_at.is_not(None),
        Conversation.last_message_at <= newest,
        Conversation.last_message_at >= oldest,
        ~answered,
    )


async def _unanswered(db, company_id, now: datetime) -> list[Condition]:
    count = await db.scalar(
        select(func.count()).select_from(Conversation).where(_unanswered_filter(company_id, now))
    )
    if not count or count < UNANSWERED_MIN_CONVERSATIONS:
        return []
    return [
        Condition(
            key="unanswered",
            severity="warning",
            title=f"{count} clientes sem resposta há mais de {UNANSWERED_MINUTES} minutos",
            body=(
                f"{count} conversas abertas com a IA ligada estão com a última mensagem do "
                f"cliente sem resposta há mais de {UNANSWERED_MINUTES} minutos. Costuma ser "
                "reinício do servidor, chave da IA sem crédito ou integração fora do ar — "
                "confira os Logs de Webhook."
            ),
        )
    ]


async def evaluate_conditions(db, company_id, now: datetime) -> list[Condition]:
    conditions: list[Condition] = []
    for check in (_failing_tools, _pending_transfers, _unanswered):
        try:
            conditions.extend(await check(db, company_id, now))
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao avaliar condição de saúde (%s)", check.__name__)
    return conditions


# ----------------------------------------------------------------- sincronia


async def sync_alerts(db, company: Company, conditions: list[Condition], now: datetime) -> tuple[int, int]:
    """Abre alerta pra condição nova, atualiza o texto da que continua e
    fecha (resolved_at) as que passaram. Retorna (abertos, fechados)."""
    active = (
        (
            await db.execute(
                select(SystemEvent).where(
                    SystemEvent.company_id == company.id,
                    SystemEvent.kind == "alert",
                    SystemEvent.resolved_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    by_key = {event.key: event for event in active}
    current_keys = {c.key for c in conditions}

    opened = 0
    for condition in conditions:
        event = by_key.get(condition.key)
        if event is not None:
            event.title = condition.title
            event.body = condition.body
            event.severity = condition.severity
            continue
        db.add(
            SystemEvent(
                company_id=company.id,
                kind="alert",
                severity=condition.severity,
                key=condition.key,
                title=condition.title,
                body=condition.body,
            )
        )
        opened += 1
        await _notify("alert", condition.severity, condition.title, condition.body, company.name)

    closed = 0
    for key, event in by_key.items():
        if key not in current_keys:
            event.resolved_at = now
            closed += 1
    await db.flush()
    return opened, closed


# ---------------------------------------------------------- relatório diário


def _day_bounds(day) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=_BRAZIL_TZ)
    return start, start + timedelta(days=1)


async def build_daily_report(db, company_id, day) -> tuple[str, str, bool]:
    """Retorna (título, corpo, teve_atividade) do dia (data de Brasília)."""
    start, end = _day_bounds(day)
    conv_filter = [
        Conversation.company_id == company_id,
        Conversation.channel != "test_console",
        Conversation.created_at >= start,
        Conversation.created_at < end,
    ]
    total = await db.scalar(select(func.count()).select_from(Conversation).where(*conv_filter)) or 0
    resolved = (
        await db.scalar(
            select(func.count()).select_from(Conversation).where(*conv_filter, Conversation.status == "resolved")
        )
        or 0
    )
    with_human = (
        await db.scalar(
            select(func.count()).select_from(Conversation).where(*conv_filter, Conversation.status == "with_human")
        )
        or 0
    )
    unanswered = (
        await db.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(
                Conversation.company_id == company_id,
                Conversation.channel != "test_console",
                Conversation.last_message_at >= start,
                Conversation.last_message_at < end,
                Conversation.status == "open",
                Conversation.ai_enabled.is_(True),
                ~exists().where(
                    Message.conversation_id == Conversation.id,
                    Message.actor.in_(("ai", "human_agent")),
                    Message.created_at >= Conversation.last_message_at,
                ),
            )
        )
        or 0
    )
    pending_now = (
        await db.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(
                Conversation.company_id == company_id,
                Conversation.transfer_pending_at.is_not(None),
                Conversation.status != "with_human",
                Conversation.channel != "test_console",
            )
        )
        or 0
    )

    failed = func.sum(case((ToolCallLog.success.is_(False), 1), else_=0))
    tool_rows = (
        await db.execute(
            select(ToolCallLog.tool_key, func.max(ToolCallLog.tool_name), func.count(), failed)
            .where(
                ToolCallLog.company_id == company_id,
                ToolCallLog.created_at >= start,
                ToolCallLog.created_at < end,
                ToolCallLog.tool_key.not_in(_IGNORED_TOOL_KEYS),
            )
            .group_by(ToolCallLog.tool_key)
        )
    ).all()
    tool_calls = sum(int(r[2]) for r in tool_rows)
    transfers_ok = sum(
        int(r[2]) - int(r[3] or 0) for r in tool_rows if r[0] == _TRANSFER_TOOL_KEY
    )
    failing = sorted(
        ((name, int(n), int(f or 0)) for _key, name, n, f in tool_rows if (f or 0) > 0),
        key=lambda item: item[2],
        reverse=True,
    )

    gaps = (
        await db.execute(
            select(KnowledgeGap.question)
            .where(
                KnowledgeGap.company_id == company_id,
                KnowledgeGap.created_at >= start,
                KnowledgeGap.created_at < end,
            )
            .order_by(KnowledgeGap.created_at.desc())
        )
    ).scalars().all()

    lines = [
        f"Conversas novas: {total}",
        f"Encerradas: {resolved} · Com atendente: {with_human} · Transferências feitas: {transfers_ok}",
        f"Clientes que ficaram sem resposta: {unanswered}",
        f"Transferências ainda pendentes agora: {pending_now}",
        f"Chamadas de ferramenta: {tool_calls} ({sum(f for _n, _t, f in failing)} com falha)",
    ]
    if failing:
        lines.append("")
        lines.append("Ferramentas com falha:")
        lines.extend(f"- {name}: {f} de {n} chamadas falharam" for name, n, f in failing[:8])
    if gaps:
        lines.append("")
        lines.append(f"Perguntas que a IA não soube responder ({len(gaps)}):")
        seen: set[str] = set()
        shown = 0
        for question in gaps:
            normalized = " ".join(question.lower().split())
            if normalized in seen:
                continue
            seen.add(normalized)
            lines.append(f"- {question.strip()[:140]}")
            shown += 1
            if shown >= 8:
                break

    title = f"Relatório de {day.strftime('%d/%m/%Y')}"
    return title, "\n".join(lines), bool(total or tool_calls or gaps)


async def ensure_daily_report(db, company: Company, now: datetime) -> bool:
    """Gera o relatório de ontem uma única vez, a partir da hora configurada
    (Brasília). Retorna True se criou."""
    local = now.astimezone(_BRAZIL_TZ)
    if local.hour < get_settings().daily_report_hour:
        return False
    day = local.date() - timedelta(days=1)
    key = f"daily:{day.isoformat()}"
    already = await db.scalar(
        select(func.count())
        .select_from(SystemEvent)
        .where(SystemEvent.company_id == company.id, SystemEvent.kind == "report", SystemEvent.key == key)
    )
    if already:
        return False
    title, body, had_activity = await build_daily_report(db, company.id, day)
    db.add(
        SystemEvent(
            company_id=company.id,
            kind="report",
            severity="info",
            key=key,
            title=title,
            body=body,
        )
    )
    await db.flush()
    if had_activity:
        await _notify("report", "info", title, body, company.name)
    return True


# ------------------------------------------------------------------ entrada


async def run_health_checks(db, now: datetime | None = None) -> None:
    """Uma passada por todas as empresas. Cada empresa é isolada: erro numa
    não impede as outras (nem a varredura de follow-up que chamou isso)."""
    now = now or datetime.now(timezone.utc)
    companies = (await db.execute(select(Company))).scalars().all()
    for company in companies:
        try:
            conditions = await evaluate_conditions(db, company.id, now)
            await sync_alerts(db, company, conditions, now)
            await ensure_daily_report(db, company, now)
            await db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("Falha no monitoramento de saúde da empresa %s", company.id)
            await db.rollback()
