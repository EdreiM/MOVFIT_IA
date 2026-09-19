"""Auditoria amostral de atendimentos — evidência real nos logs, vitrine para sócios."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Message, Tool, ToolCallLog
from app.schemas import ShowcaseExample, ShowcaseSnippet
from app.services.message_flow import _is_guest_tool, _is_payment_link_tool

_MAX_SNIPPETS = 5
_MAX_TEXT_LEN = 320

_MODALITY_CATALOG: list[dict] = [
    {
        "id": "plans",
        "title": "Planos e valores",
        "outcome": "Cliente recebeu planos com imagem, preço e benefícios.",
        "tool_keys": ["enviar_imagens_planos"],
    },
    {
        "id": "physical_eval",
        "title": "Avaliação física",
        "outcome": "Horários consultados e/ou avaliação confirmada no sistema.",
        "tool_keys": ["insere_agenda_avalicao", "consultar_agendamento_horarios"],
    },
    {
        "id": "student_verify",
        "title": "Identificação de aluno",
        "outcome": "Unidade e status de aluno confirmados a partir do CPF.",
        "tool_keys": ["verificar_unidade_por_cpf"],
    },
    {
        "id": "guests",
        "title": "Convidados",
        "outcome": "Consulta de convites ou convidados respondida com dados do sistema.",
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
        "outcome": "Atendimento encerrado pela IA sem transferência para humano.",
        "tool_keys": ["encerrar_atendimento"],
    },
]


@dataclass
class _AuditHit:
    conversation_id: UUID
    occurred_at: datetime
    evidence: str


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


async def _latest_tool_hit(
    db: AsyncSession,
    company_id: UUID,
    tool_keys: list[str],
) -> _AuditHit | None:
    row = await db.execute(
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
        .limit(1)
    )
    hit = row.first()
    if not hit:
        return None
    return _AuditHit(
        conversation_id=hit.conversation_id,
        occurred_at=hit.created_at,
        evidence=_tool_log_evidence(hit.tool_key, hit.tool_name),
    )


async def _heuristic_tool_keys(db: AsyncSession, company_id: UUID, predicate) -> list[str]:
    result = await db.execute(
        select(Tool).where(Tool.company_id == company_id, Tool.is_active.is_(True))
    )
    return [tool.tool_key for tool in result.scalars().all() if predicate(tool)]


async def _latest_plans_from_messages(db: AsyncSession, company_id: UUID) -> _AuditHit | None:
    result = await db.execute(
        select(Message.conversation_id, Message.created_at)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Message.company_id == company_id,
            Message.actor == "ai",
            Message.content_type == "image",
            Conversation.channel != "test_console",
        )
        .order_by(Message.created_at.desc())
        .limit(20)
    )
    for conversation_id, created_at in result.all():
        msg = await db.scalar(
            select(Message).where(
                Message.conversation_id == conversation_id,
                Message.created_at == created_at,
            )
        )
        if msg and (msg.raw_payload or {}).get("images"):
            return _AuditHit(
                conversation_id=conversation_id,
                occurred_at=created_at,
                evidence="Registro de mensagem · imagem de plano com metadados · envio confirmado",
            )
    return None


async def _find_modality_hit(
    db: AsyncSession,
    company_id: UUID,
    modality: dict,
) -> _AuditHit | None:
    tool_keys = modality.get("tool_keys") or []
    if tool_keys:
        hit = await _latest_tool_hit(db, company_id, tool_keys)
        if hit:
            return hit
        if modality["id"] == "plans":
            return await _latest_plans_from_messages(db, company_id)
        return None

    heuristic = modality.get("heuristic")
    if heuristic == "guest":
        keys = await _heuristic_tool_keys(db, company_id, _is_guest_tool)
    elif heuristic == "payment_link":
        keys = await _heuristic_tool_keys(db, company_id, _is_payment_link_tool)
    else:
        return None
    if not keys:
        return None
    return await _latest_tool_hit(db, company_id, keys)


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


async def compute_metrics_showcase(db: AsyncSession, company_id: UUID) -> list[ShowcaseExample]:
    records: list[ShowcaseExample] = []
    for modality in _MODALITY_CATALOG:
        hit = await _find_modality_hit(db, company_id, modality)
        if not hit:
            continue
        snippets = await _build_snippets(db, hit.conversation_id, around=hit.occurred_at)
        if len(snippets) < 2:
            continue
        records.append(
            ShowcaseExample(
                modality=modality["id"],
                title=modality["title"],
                outcome=modality["outcome"],
                evidence=hit.evidence,
                occurred_at=hit.occurred_at,
                snippets=snippets,
            )
        )
    records.sort(key=lambda item: item.occurred_at, reverse=True)
    return records
