from datetime import datetime
from typing import Any

from app.adapters.base import NormalizedMessageEvent


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _normalize_phone(value: str | None) -> str | None:
    if not value:
        return None
    clean = value.strip()
    if "@s.whatsapp.net" in clean:
        clean = clean.split("@", 1)[0]
    elif "@lid" in clean:
        return None

    digits = "".join(ch for ch in clean if ch.isdigit())
    if not digits:
        return None
    return f"+{digits}"


def _extract_text(message: dict[str, Any]) -> str | None:
    if not isinstance(message, dict):
        return None
    if isinstance(message.get("conversation"), str):
        return message["conversation"].strip() or None

    extended = message.get("extendedTextMessage") or {}
    if isinstance(extended.get("text"), str):
        return extended["text"].strip() or None

    image = message.get("imageMessage") or {}
    if isinstance(image.get("caption"), str):
        return image["caption"].strip() or None

    document = message.get("documentMessage") or {}
    if isinstance(document.get("caption"), str):
        return document["caption"].strip() or None

    return None


def adapt_evolution_api(
    payload: dict[str, Any],
    integration_config: dict[str, Any] | None = None,
) -> NormalizedMessageEvent:
    body = payload.get("body") or payload
    data = body.get("data") or {}
    info = data.get("Info") or {}
    message = data.get("Message") or {}
    integration_config = integration_config or {}

    is_from_me = bool(info.get("IsFromMe"))
    event_type = "message_outbound" if is_from_me else "message_inbound"
    actor = "human_agent" if is_from_me else "customer"

    content_type = (info.get("Type") or "text").lower()
    if content_type not in {"text", "image", "audio", "file", "location"}:
        content_type = "text"

    contact_phone = None
    if is_from_me:
        contact_phone = _normalize_phone(info.get("RecipientAlt"))
        if not contact_phone:
            contact_phone = _normalize_phone(info.get("Chat"))
    else:
        contact_phone = _normalize_phone(info.get("Sender"))
        if not contact_phone:
            contact_phone = _normalize_phone(info.get("Chat"))

    external_conversation_id = _normalize_phone(info.get("Chat")) or contact_phone
    channel_to = (
        integration_config.get("phone_number")
        or integration_config.get("channel_to")
        or integration_config.get("number")
    )

    return NormalizedMessageEvent(
        event_type=event_type,  # type: ignore[arg-type]
        external_message_id=str(info.get("ID")) if info.get("ID") else None,
        external_conversation_id=external_conversation_id,
        channel_to=channel_to,
        contact_phone=contact_phone,
        content_type=content_type,  # type: ignore[arg-type]
        text=_extract_text(message),
        timestamp=_parse_ts(info.get("Timestamp")),
        actor=actor,  # type: ignore[arg-type]
        human_handoff_detected=is_from_me,
        raw_payload=payload,
        contact_name=info.get("PushName") if not is_from_me else None,
    )
