"""Exporta conversas em um .zip com texto legível — pra revisar onde a IA
errou (mensagens + chamadas de ferramenta na ordem em que aconteceram)."""
from __future__ import annotations

import io
import json
import re
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Lead, Message, ToolCallLog

MAX_CONVERSATIONS = 500
_BRAZIL_TZ = timezone(timedelta(hours=-3))

from app.services.message_flow import TOOL_KEY_CHECK_SESSION

# Chamada interna do follow-up, não é algo que a IA decidiu — só ruído.
_INTERNAL_TOOL_KEYS = {TOOL_KEY_CHECK_SESSION}

_CPF_RE = re.compile(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# 10–13 dígitos seguidos (telefone com/sem DDI) — roda depois do CPF (11).
_PHONE_RE = re.compile(r"(?<!\d)\+?\d{10,13}(?!\d)")


def mask_pii(text: str | None) -> str:
    """CPF, e-mail e telefone viram marcadores — o que importa pra revisar a
    conversa é que o dado FOI informado, não o valor."""
    if not text:
        return ""
    text = _EMAIL_RE.sub("[email]", text)
    text = _CPF_RE.sub("[CPF]", text)
    return _PHONE_RE.sub("[telefone]", text)


def mask_phone(phone: str | None) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 8:
        return "[telefone]"
    return f"{digits[:4]}****{digits[-4:]}"


def _fmt_dt(value: datetime | None) -> str:
    if not value:
        return "--/-- --:--"
    return value.astimezone(_BRAZIL_TZ).strftime("%d/%m %H:%M")


_ACTOR_LABEL = {"customer": "CLIENTE", "ai": "IA", "human_agent": "ATENDENTE", "system": "SISTEMA"}


def build_export_text(
    conversations: list[Conversation],
    messages_by_conv: dict[UUID, list[Message]],
    tools_by_conv: dict[UUID, list[ToolCallLog]],
    units_by_phone: dict[str, str | None],
    *,
    mask: bool,
    days: int,
    truncated: bool,
) -> str:
    def clean(value: str | None) -> str:
        return mask_pii(value) if mask else (value or "")

    now = datetime.now(_BRAZIL_TZ).strftime("%d/%m/%Y %H:%M")
    lines = [
        f"EXPORTAÇÃO DE CONVERSAS — gerada em {now} (horário de Brasília)",
        f"Período: últimos {days} dia(s) · {len(conversations)} conversa(s)"
        + (f" (limitado às {MAX_CONVERSATIONS} mais recentes)" if truncated else ""),
        f"Dados pessoais mascarados: {'sim' if mask else 'NÃO'}",
        "Legenda: [ferramenta ✓/✗] = chamada de ferramenta pela IA, na ordem em que ocorreu.",
        "",
    ]

    for conv in conversations:
        phone = mask_phone(conv.contact_phone) if mask else conv.contact_phone
        name = "" if mask else (conv.contact_name or "")
        unit = units_by_phone.get(re.sub(r"\D", "", conv.contact_phone or ""))
        lines.append("=" * 78)
        lines.append(
            f"CONVERSA {conv.id} · {phone}{' · ' + name if name else ''}"
        )
        lines.append(
            f"status={conv.status} · ia={'ligada' if conv.ai_enabled else 'pausada'}"
            f" · unidade={unit or '-'} · início={_fmt_dt(conv.created_at)}"
        )
        if conv.handoff_summary:
            lines.append(f"resumo da transferência: {clean(conv.handoff_summary)}")
        lines.append("-" * 78)

        events: list[tuple[datetime, str]] = []
        for m in messages_by_conv.get(conv.id, []):
            label = _ACTOR_LABEL.get(m.actor, m.actor.upper())
            body = clean(m.text) or f"<{m.content_type} sem texto>"
            events.append((m.created_at, f"{_fmt_dt(m.created_at)} {label}: {body}"))
        for t in tools_by_conv.get(conv.id, []):
            if t.tool_key in _INTERNAL_TOOL_KEYS:
                continue
            args = clean(json.dumps(t.arguments or {}, ensure_ascii=False))
            err = f" — {clean(t.error_message)}" if t.error_message else ""
            events.append(
                (
                    t.created_at,
                    f"{_fmt_dt(t.created_at)} [ferramenta {'✓' if t.success else '✗'}] "
                    f"{t.tool_key} {args}{err}",
                )
            )
        events.sort(key=lambda e: e[0])
        lines.extend(text for _, text in events)
        lines.append("")

    return "\n".join(lines)


async def export_conversations_zip(
    db: AsyncSession,
    company_id: UUID,
    *,
    days: int = 30,
    status: str | None = None,
    ai_enabled: bool | None = None,
    mask: bool = True,
) -> tuple[bytes, int]:
    """Retorna (bytes do .zip, quantidade de conversas exportadas)."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = select(Conversation).where(
        Conversation.company_id == company_id,
        Conversation.channel != "test_console",
        Conversation.last_message_at >= since,
    )
    if status:
        stmt = stmt.where(Conversation.status == status)
    if ai_enabled is not None:
        stmt = stmt.where(Conversation.ai_enabled.is_(ai_enabled))
    stmt = stmt.order_by(Conversation.last_message_at.desc()).limit(MAX_CONVERSATIONS + 1)
    conversations = list((await db.execute(stmt)).scalars().all())
    truncated = len(conversations) > MAX_CONVERSATIONS
    conversations = conversations[:MAX_CONVERSATIONS]
    # Leitura mais antiga primeiro, pra acompanhar o raciocínio cronológico.
    conversations.sort(key=lambda c: c.created_at)

    messages_by_conv: dict[UUID, list[Message]] = defaultdict(list)
    tools_by_conv: dict[UUID, list[ToolCallLog]] = defaultdict(list)
    units_by_phone: dict[str, str | None] = {}
    conv_ids = [c.id for c in conversations]
    if conv_ids:
        msg_rows = await db.execute(
            select(Message)
            .where(Message.conversation_id.in_(conv_ids))
            .order_by(Message.created_at.asc())
        )
        for m in msg_rows.scalars():
            messages_by_conv[m.conversation_id].append(m)
        tool_rows = await db.execute(
            select(ToolCallLog)
            .where(ToolCallLog.conversation_id.in_(conv_ids))
            .order_by(ToolCallLog.created_at.asc())
        )
        for t in tool_rows.scalars():
            tools_by_conv[t.conversation_id].append(t)
        lead_rows = await db.execute(select(Lead).where(Lead.company_id == company_id))
        for lead in lead_rows.scalars():
            units_by_phone[re.sub(r"\D", "", lead.phone or "")] = lead.unit

    text = build_export_text(
        conversations,
        messages_by_conv,
        tools_by_conv,
        units_by_phone,
        mask=mask,
        days=days,
        truncated=truncated,
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("conversas.txt", text)
    return buffer.getvalue(), len(conversations)
