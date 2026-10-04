"""Promoções configuráveis no painel — leitura, envio e transferência determinística."""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Message, Promotion, Tool
from app.services.text_normalize import normalize_text as _normalize_text
from app.services.text_normalize import normalize_tokens as _normalize_tokens

logger = logging.getLogger(__name__)

_BRAZIL_TZ = ZoneInfo("America/Sao_Paulo")

_DEFAULT_TRIGGER_KEYWORDS = (
    "promocao",
    "promo",
    "desconto",
    "campanha",
    "condicao especial",
    "outubro rosa",
)

_WOMAN_CONTEXT_TOKENS = {
    "mulher",
    "mulheres",
    "feminino",
    "sou mulher",
    "sou uma mulher",
    "outubro",
    "rosa",
}

_CONFIRM_WORDS = {
    "sim",
    "quero",
    "gostaria",
    "pode",
    "bora",
    "vamos",
    "aceito",
    "me interessa",
    "tenho interesse",
    "pode ser",
    "isso",
    "essa",
    "esse",
}


def _today_local() -> date:
    return datetime.now(_BRAZIL_TZ).date()


def _promotion_in_date_range(promotion: Promotion, today: date) -> bool:
    if promotion.valid_from and today < promotion.valid_from:
        return False
    if promotion.valid_until and today > promotion.valid_until:
        return False
    return True


def _promotion_unit_matches(promotion: Promotion, unit_id: UUID | None) -> bool:
    raw_ids = promotion.unit_ids or []
    if not raw_ids:
        return True
    if unit_id is None:
        return True
    return str(unit_id) in {str(item) for item in raw_ids}


async def get_active_promotions(
    db: AsyncSession,
    company_id: UUID,
    *,
    unit_id: UUID | None = None,
    mention_on_plan_request: bool | None = None,
    today: date | None = None,
) -> list[Promotion]:
    result = await db.execute(
        select(Promotion).where(
            Promotion.company_id == company_id,
            Promotion.is_active.is_(True),
        )
    )
    today = today or _today_local()
    promotions = [
        p
        for p in result.scalars().all()
        if _promotion_in_date_range(p, today) and _promotion_unit_matches(p, unit_id)
    ]
    if mention_on_plan_request is not None:
        promotions = [p for p in promotions if p.mention_on_plan_request == mention_on_plan_request]
    promotions.sort(key=lambda p: (p.sort_order, p.created_at))
    return promotions


def _promotion_trigger_keywords(promotion: Promotion) -> set[str]:
    custom = [_normalize_text(k) for k in (promotion.trigger_keywords or []) if k and k.strip()]
    if custom:
        return set(custom)
    return set(_DEFAULT_TRIGGER_KEYWORDS)


def wants_promotion_inquiry(text: str, promotions: list[Promotion] | None = None) -> bool:
    normalized = _normalize_text(text)
    tokens = _normalize_tokens(text)
    if any(k in normalized or k in tokens for k in _DEFAULT_TRIGGER_KEYWORDS):
        return True
    if not promotions:
        return False
    for promotion in promotions:
        for keyword in _promotion_trigger_keywords(promotion):
            if keyword in normalized or keyword in tokens:
                return True
            if any(part in tokens for part in keyword.split() if len(part) >= 4):
                return True
    return False


def _woman_context_in_text(text: str) -> bool:
    normalized = _normalize_text(text)
    tokens = _normalize_tokens(text)
    if "outubro rosa" in normalized:
        return True
    if tokens & _WOMAN_CONTEXT_TOKENS:
        return True
    if "sou" in tokens and "mulher" in tokens:
        return True
    return False


def woman_context_in_conversation(text: str, recent_customer_texts: list[str] | None) -> bool:
    if _woman_context_in_text(text):
        return True
    for prior in reversed((recent_customer_texts or [])[-6:]):
        if prior and _woman_context_in_text(prior):
            return True
    return False


def _confirms_interest(text: str) -> bool:
    normalized = _normalize_text(text)
    tokens = _normalize_tokens(text)
    if tokens & _CONFIRM_WORDS:
        return True
    if any(phrase in normalized for phrase in ("quero essa", "quero esse", "me interessa", "tenho interesse")):
        return True
    return False


def _audience_label(audience: str) -> str:
    return {
        "all": "todos os clientes",
        "women": "mulheres",
        "men": "homens",
        "new_students": "novas alunas/os",
    }.get(audience, audience)


def format_promotions_prompt_block(promotions: list[Promotion]) -> str:
    if not promotions:
        return ""
    lines = [
        "[Promoções ativas — cadastro oficial do painel]",
        "Use SOMENTE estas promoções quando o assunto for planos ou promoções. "
        "Não invente campanhas, grupos VIP ou links que não estejam aqui.",
        "",
    ]
    for promotion in promotions:
        lines.append(f"• {promotion.title} (público: {_audience_label(promotion.audience)})")
        lines.append(f"  Texto: {promotion.message.strip()}")
        if promotion.requires_transfer:
            reason = promotion.transfer_reason or f"Interesse na promoção {promotion.title}"
            lines.append(
                f"  Sem link de matrícula — se o cliente confirmar interesse "
                f"({'e for mulher, se público=mulheres' if promotion.audience == 'women' else 'após elegibilidade'}), "
                f"chame transferir_atendimento com motivo: {reason}"
            )
        lines.append("")
    return "\n".join(lines).strip()


async def _get_sent_promotion_ids(db: AsyncSession, conversation_id: UUID) -> set[str]:
    result = await db.execute(
        select(Message.raw_payload).where(
            Message.conversation_id == conversation_id,
            Message.actor == "ai",
        )
    )
    sent: set[str] = set()
    for (payload,) in result.all():
        if isinstance(payload, dict) and payload.get("promotion_id"):
            sent.add(str(payload["promotion_id"]))
    return sent


async def send_promotion_to_client(
    db: AsyncSession,
    conversation: Conversation,
    promotion: Promotion,
) -> None:
    from app.adapters.base import NormalizedMessageEvent
    from app.services.message_flow import (
        _resolve_plan_images_tool,
        _send_single_plan_image,
        save_message,
        send_outbound,
    )

    is_test = conversation.channel == "test_console"
    company_id = conversation.company_id

    if promotion.image_url:
        plan_tool = await _resolve_plan_images_tool(db, conversation)
        image_payload = {
            "unidade": "Promoção",
            "plano": promotion.title,
            "url": promotion.image_url,
        }
        sent = False
        if plan_tool and plan_tool.webhook_url:
            sent = await _send_single_plan_image(
                db,
                plan_tool,
                image_payload,
                conversation,
                raw_payload_extra={
                    "promotion_id": str(promotion.id),
                    "promotion_banner": True,
                },
            )
        if not sent:
            logger.warning(
                "Banner da promoção %s não enviado — ferramenta enviar_imagens_planos ausente ou falhou",
                promotion.title,
            )
            await save_message(
                db,
                conversation,
                NormalizedMessageEvent(
                    event_type="message_outbound",
                    external_message_id=None,
                    external_conversation_id=conversation.external_conversation_id,
                    channel_to=None,
                    contact_phone=conversation.contact_phone,
                    content_type="image",
                    text=promotion.title,
                    timestamp=datetime.now(timezone.utc),
                    actor="ai",
                    raw_payload={
                        "promotion_id": str(promotion.id),
                        "image_url": promotion.image_url,
                        "generated": True,
                        "promotion_banner": True,
                        "delivery_failed": True,
                    },
                ),
            )

    await save_message(
        db,
        conversation,
        NormalizedMessageEvent(
            event_type="message_outbound",
            external_message_id=None,
            external_conversation_id=conversation.external_conversation_id,
            channel_to=None,
            contact_phone=conversation.contact_phone,
            content_type="text",
            text=promotion.message.strip(),
            timestamp=datetime.now(timezone.utc),
            actor="ai",
            raw_payload={"promotion_id": str(promotion.id), "generated": True, "promotion_message": True},
        ),
    )
    if not is_test:
        await send_outbound(db, company_id, conversation, promotion.message.strip())


async def present_promotions(
    db: AsyncSession,
    conversation: Conversation,
    promotions: list[Promotion],
) -> int:
    if not promotions:
        return 0
    sent_ids = await _get_sent_promotion_ids(db, conversation.id)
    count = 0
    for promotion in promotions:
        pid = str(promotion.id)
        if pid in sent_ids:
            continue
        await send_promotion_to_client(db, conversation, promotion)
        sent_ids.add(pid)
        count += 1
    return count


async def find_recent_offered_promotion(
    db: AsyncSession,
    conversation: Conversation,
    history: list[Message],
) -> Promotion | None:
    promotion_ids: list[str] = []
    for message in reversed(history):
        if message.actor != "ai":
            continue
        payload = message.raw_payload if isinstance(message.raw_payload, dict) else {}
        if payload.get("promotion_id"):
            promotion_ids.append(str(payload["promotion_id"]))
            break
    if not promotion_ids:
        return None
    promotion = await db.get(Promotion, UUID(promotion_ids[0]))
    if not promotion or not promotion.is_active:
        return None
    if not _promotion_in_date_range(promotion, _today_local()):
        return None
    return promotion


def promotion_transfer_active(
    user_text: str,
    history: list[Message],
    recent_customer_texts: list[str] | None,
    promotion: Promotion | None,
) -> bool:
    if not promotion or not promotion.requires_transfer:
        return False
    if not _confirms_interest(user_text):
        return False
    if promotion.audience == "women" and not woman_context_in_conversation(user_text, recent_customer_texts):
        return False
    offered = any(
        isinstance(m.raw_payload, dict) and str(m.raw_payload.get("promotion_id")) == str(promotion.id)
        for m in history
        if m.actor == "ai"
    )
    return offered


async def run_promotion_transfer_pipeline(
    db: AsyncSession,
    conversation: Conversation,
    promotion: Promotion,
    tools_by_key: dict[str, Tool],
) -> str:
    from app.services.message_flow import TOOL_KEY_TRANSFER, execute_tool

    transfer_tool = tools_by_key.get(TOOL_KEY_TRANSFER)
    reason = (
        promotion.transfer_reason
        or f"Cliente interessado(a) na promoção {promotion.title} — matrícula/condição especial sem link online."
    )
    reply = (
        f"Perfeito! Vou te encaminhar para um atendente que finaliza a matrícula com a condição "
        f"*{promotion.title}* — é um plano/promo especial e precisa ser feito com a equipe. 😊"
    )
    if not transfer_tool or not transfer_tool.webhook_url:
        return (
            f"{reply} "
            "Se preferir, fale com a recepção da unidade que alguém te ajuda com essa condição especial."
        )
    if conversation.status != "with_human":
        await execute_tool(db, transfer_tool, {"motivo": reason}, conversation)
    return f"{reply} Um atendente continua com você em instantes! 😊"
