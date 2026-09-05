from datetime import datetime
from typing import Any

from app.adapters.base import NormalizedMessageEvent


def _dig(data: Any, path: str) -> Any:
    """Resolve caminho estilo a.b.c em dicts aninhados."""
    if not path:
        return None
    current = data
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def adapt_generic_mapping(payload: dict[str, Any], mapping: dict[str, Any] | None) -> NormalizedMessageEvent:
    mapping = mapping or {}
    text = _dig(payload, mapping.get("text", "text"))
    contact_phone = _dig(payload, mapping.get("contact_phone", "from"))
    channel_to = _dig(payload, mapping.get("channel_to", "to"))
    external_message_id = _dig(payload, mapping.get("external_message_id", "id"))
    external_conversation_id = _dig(payload, mapping.get("external_conversation_id", "conversation_id"))
    content_type = (_dig(payload, mapping.get("content_type", "content_type")) or "text").lower()
    actor = (_dig(payload, mapping.get("actor", "actor")) or "customer").lower()
    event_raw = (_dig(payload, mapping.get("event_type", "event_type")) or "message_inbound").lower()
    ts_raw = _dig(payload, mapping.get("timestamp", "timestamp"))
    handoff = bool(_dig(payload, mapping.get("human_handoff_detected", "human_handoff_detected")))

    if event_raw in {"message_inbound", "inbound", "received"}:
        event_type = "message_inbound"
    elif event_raw in {"message_outbound", "outbound", "sent"}:
        event_type = "message_outbound"
    else:
        event_type = "status_update"

    timestamp = None
    if isinstance(ts_raw, str):
        try:
            timestamp = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except ValueError:
            timestamp = None

    return NormalizedMessageEvent(
        event_type=event_type,  # type: ignore[arg-type]
        external_message_id=str(external_message_id) if external_message_id else None,
        external_conversation_id=str(external_conversation_id) if external_conversation_id else None,
        channel_to=str(channel_to) if channel_to else None,
        contact_phone=str(contact_phone) if contact_phone else None,
        content_type=content_type if content_type in {"text", "image", "audio", "file", "location"} else "text",  # type: ignore[arg-type]
        text=str(text) if text is not None else None,
        timestamp=timestamp,
        actor=actor if actor in {"customer", "ai", "human_agent", "system"} else "customer",  # type: ignore[arg-type]
        human_handoff_detected=handoff,
        raw_payload=payload,
    )
