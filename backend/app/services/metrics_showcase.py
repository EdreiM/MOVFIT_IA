"""Auditoria de atendimentos corretos — só registros com desfecho comprovado."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Lead, Message, Tool, ToolCallLog
from app.schemas import ShowcaseExample, ShowcaseSnippet
from app.services.message_flow import (
    TOOL_KEY_BOOK_PHYSICAL_EVAL,
    TOOL_KEY_END,
    TOOL_KEY_SEND_PLAN_IMAGES,
    TOOL_KEY_TRANSFER,
    TOOL_KEY_VERIFY_UNIT_BY_CPF,
    _is_guest_tool,
    _is_payment_link_tool,
    sanitize_phone_digits,
)

_MAX_SNIPPETS = 5
_MAX_TEXT_LEN = 320
_CANDIDATE_LIMIT = 40

_MODALITY_CATALOG: list[dict] = [
    {
        "id": "plans",
        "title": "Planos e valores",
        "outcome": "Cliente recebeu planos com imagem, preço e benefícios.",
        "tool_keys": [TOOL_KEY_SEND_PLAN_IMAGES],
    },
    {
        "id": "physical_eval",
        "title": "Avaliação física",
        "outcome": "Avaliação física confirmada e registrada no sistema.",
        "tool_keys": [TOOL_KEY_BOOK_PHYSICAL_EVAL],
    },
    {
        "id": "student_verify",
        "title": "Identificação de aluno",
        "outcome": "Unidade e status de aluno confirmados a partir do CPF.",
        "tool_keys": [TOOL_KEY_VERIFY_UNIT_BY_CPF],
    },
    {
        "id": "guests",
        "title": "Convidados",
        "outcome": "Consulta de convites respondida com dados do sistema.",
        "heuristic": "guest",
    },
    {
        "id": "payment_link",
        "title": "Link de pagamento",
        "outcome": "Link de parcela ou boleto gerado e repassado ao cliente.",
        "heuristic": "payment_link",
    },
    {
        "id": "resolved",
        "title": "Encerramento pela IA",
        "outcome": "Dúvida respondida e atendimento encerrado sem transferir para humano.",
        "tool_keys": [TOOL_KEY_END],
    },
]


@dataclass
class _AuditHit:
    conversation_id: UUID
    occurred_at: datetime
    evidence: str
    tool_key: str | None = None


def _anonymize_text(text: str) -> str:
    text = re.sub(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b", "***.***.***-**", text)
    text = re.sub(r"\b\d{11}\b", "***.***.***-**", text)
    text = re.sub(r"\+?55\s?\(?\d{2}\)?\s?\d{4,5}-?\d{4}", "(**) *****-****", text)
    text = re.sub(r"\b[\w.-]+@[\w.-]+\.\w+\b", "***@***.***", text)
    return text


def _trim_text(text: str) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= _MAX_TEXT_LEN:
        return cleaned
    return cleaned[: _MAX_TEXT_LEN - 1].rstrip() + "…"


def _message_display_text(message: Message) -> str | None:
    payload = message.raw_payload or {}
    if message.content_type == "text" and message.text:
        return _trim_text(message.text)
    if message.content_type == "image":
        images = payload.get("images") or []
        if images:
            labels: list[str] = []
            for item in images:
                if not isinstance(item, dict):
                    continue
                label = item.get("plano") or item.get("unidade")
                if label:
                    labels.append(str(label))
            if labels:
                joined = ", ".join(labels[:3])
                suffix = "…" if len(labels) > 3 else ""
                return f"[Registro] Imagem de plano enviada: {joined}{suffix}"
        if message.text:
            return _trim_text(message.text)
    return None


def _tool_log_evidence(tool_key: str, tool_name: str | None) -> str:
    label = tool_name or tool_key
    return f"Log de ferramenta · {label} ({tool_key}) · execução bem-sucedida"


async def _get_conversation(db: AsyncSession, conversation_id: UUID) -> Conversation | None:
    return await db.get(Conversation, conversation_id)


async def _get_lead_for_conversation(db: AsyncSession, conversation: Conversation) -> Lead | None:
    phone = sanitize_phone_digits(conversation.contact_phone)
    if not phone:
        return None
    return await db.scalar(
        select(Lead).where(Lead.company_id == conversation.company_id, Lead.phone == phone)
    )


async def _conversation_had_transfer(db: AsyncSession, conversation_id: UUID) -> bool:
    conversation = await _get_conversation(db, conversation_id)
    if not conversation:
        return True
    if conversation.status == "with_human" or conversation.ai_enabled is False:
        return True
    transferred = await db.scalar(
        select(func.count())
        .select_from(ToolCallLog)
        .where(
            ToolCallLog.conversation_id == conversation_id,
            ToolCallLog.tool_key == TOOL_KEY_TRANSFER,
            ToolCallLog.success.is_(True),
        )
    )
    return (transferred or 0) > 0


async def _has_plan_delivery(db: AsyncSession, conversation_id: UUID) -> bool:
    result = await db.execute(
        select(Message).where(
            Message.conversation_id == conversation_id,
            Message.actor == "ai",
            Message.content_type == "image",
        )
    )
    for message in result.scalars().all():
        if (message.raw_payload or {}).get("images"):
            return True
    return False


async def _has_ai_reply_after(
    db: AsyncSession,
    conversation_id: UUID,
    *,
    after: datetime,
    min_len: int = 20,
    require_url: bool = False,
) -> bool:
    result = await db.execute(
        select(Message).where(
            Message.conversation_id == conversation_id,
            Message.actor == "ai",
            Message.direction == "outbound",
            Message.created_at >= after,
            Message.content_type == "text",
        )
    )
    for message in result.scalars().all():
        text = (message.text or "").strip()
        if len(text) < min_len:
            continue
        if require_url and "http" not in text.lower():
            continue
        return True
    return False


async def _verify_correct_outcome(
    db: AsyncSession,
    hit: _AuditHit,
    modality: dict,
) -> bool:
    conversation = await _get_conversation(db, hit.conversation_id)
    if not conversation or conversation.channel == "test_console":
        return False
    if await _conversation_had_transfer(db, hit.conversation_id):
        return False

    modality_id = modality["id"]
    lead = await _get_lead_for_conversation(db, conversation)

    if modality_id == "plans":
        if hit.tool_key != TOOL_KEY_SEND_PLAN_IMAGES:
            return False
        return await _has_plan_delivery(db, hit.conversation_id)

    if modality_id == "physical_eval":
        if hit.tool_key != TOOL_KEY_BOOK_PHYSICAL_EVAL:
            return False
        return bool(lead and lead.physical_eval_scheduled)

    if modality_id == "student_verify":
        if hit.tool_key != TOOL_KEY_VERIFY_UNIT_BY_CPF:
            return False
        return bool(lead and (lead.is_student or lead.unit))

    if modality_id == "guests":
        return await _has_ai_reply_after(db, hit.conversation_id, after=hit.occurred_at, min_len=15)

    if modality_id == "payment_link":
        return await _has_ai_reply_after(
            db,
            hit.conversation_id,
            after=hit.occurred_at,
            min_len=10,
            require_url=True,
        )

    if modality_id == "resolved":
        if hit.tool_key != TOOL_KEY_END:
            return False
        return conversation.status == "resolved" and conversation.ai_enabled is True

    return False


async def _list_tool_hits(
    db: AsyncSession,
    company_id: UUID,
    tool_keys: list[str],
) -> list[_AuditHit]:
    result = await db.execute(
        select(
            ToolCallLog.conversation_id,
            ToolCallLog.created_at,
            ToolCallLog.tool_key,
            ToolCallLog.tool_name,
        )
        .join(Conversation, Conversation.id == ToolCallLog.conversation_id)
        .where(
            ToolCallLog.company_id == company_id,
            ToolCallLog.tool_key.in_(tool_keys),
            ToolCallLog.success.is_(True),
            Conversation.channel != "test_console",
        )
        .order_by(ToolCallLog.created_at.desc())
        .limit(_CANDIDATE_LIMIT)
    )
    hits: list[_AuditHit] = []
    for row in result.all():
        hits.append(
            _AuditHit(
                conversation_id=row.conversation_id,
                occurred_at=row.created_at,
                evidence=_tool_log_evidence(row.tool_key, row.tool_name),
                tool_key=row.tool_key,
            )
        )
    return hits


async def _heuristic_tool_keys(db: AsyncSession, company_id: UUID, predicate) -> list[str]:
    result = await db.execute(
        select(Tool).where(Tool.company_id == company_id, Tool.is_active.is_(True))
    )
    return [tool.tool_key for tool in result.scalars().all() if predicate(tool)]


async def _find_correct_modality_hit(
    db: AsyncSession,
    company_id: UUID,
    modality: dict,
) -> _AuditHit | None:
    tool_keys = modality.get("tool_keys") or []
    if not tool_keys:
        heuristic = modality.get("heuristic")
        if heuristic == "guest":
            tool_keys = await _heuristic_tool_keys(db, company_id, _is_guest_tool)
        elif heuristic == "payment_link":
            tool_keys = await _heuristic_tool_keys(db, company_id, _is_payment_link_tool)
        else:
            return None
        if not tool_keys:
            return None

    for hit in await _list_tool_hits(db, company_id, tool_keys):
        if await _verify_correct_outcome(db, hit, modality):
            return hit
    return None


async def _build_snippets(
    db: AsyncSession,
    conversation_id: UUID,
    *,
    around: datetime | None,
) -> list[ShowcaseSnippet]:
    result = await db.execute(
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.actor.in_(["customer", "ai"]),
        )
        .order_by(Message.created_at.asc())
    )
    messages = result.scalars().all()
    if around:
        filtered = [m for m in messages if m.created_at <= around]
        if filtered:
            messages = filtered
    snippets: list[ShowcaseSnippet] = []
    for message in messages:
        text = _message_display_text(message)
        if not text:
            continue
        snippets.append(
            ShowcaseSnippet(
                actor=message.actor,
                text=_anonymize_text(text),
            )
        )
    if len(snippets) > _MAX_SNIPPETS:
        snippets = snippets[-_MAX_SNIPPETS:]
    return snippets


def _snippets_show_both_sides(snippets: list[ShowcaseSnippet]) -> bool:
    actors = {snippet.actor for snippet in snippets}
    return "customer" in actors and "ai" in actors


async def compute_metrics_showcase(db: AsyncSession, company_id: UUID) -> list[ShowcaseExample]:
    records: list[ShowcaseExample] = []
    for modality in _MODALITY_CATALOG:
        hit = await _find_correct_modality_hit(db, company_id, modality)
        if not hit:
            continue
        snippets = await _build_snippets(db, hit.conversation_id, around=hit.occurred_at)
        if len(snippets) < 2 or not _snippets_show_both_sides(snippets):
            continue
        records.append(
            ShowcaseExample(
                modality=modality["id"],
                title=modality["title"],
                outcome=modality["outcome"],
                evidence=f"{hit.evidence} · desfecho correto verificado",
                occurred_at=hit.occurred_at,
                snippets=snippets,
            )
        )
    records.sort(key=lambda item: item.occurred_at, reverse=True)
    return records
