from datetime import datetime
from typing import Any

from app.adapters.base import NormalizedMessageEvent


CONTENT_MAP = {
    "TEXT": "text",
    "IMAGE": "image",
    "AUDIO": "audio",
    "FILE": "file",
    "DOCUMENT": "file",
    "LOCATION": "location",
}


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # ms or seconds
        ts = float(value)
        if ts > 1e12:
            ts /= 1000
        return datetime.utcfromtimestamp(ts)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def adapt_movfit_hub(payload: dict[str, Any]) -> NormalizedMessageEvent:
    """Adaptador dedicado do hub Mov Fit (produção)."""
    body = payload.get("body") or payload
    content = body.get("content") or {}
    details = content.get("details") or {}

    event_raw = (body.get("eventType") or body.get("event_type") or "").upper()
    if event_raw in {"MESSAGE_RECEIVED", "MESSAGE_INBOUND"}:
        event_type = "message_inbound"
    elif event_raw in {"MESSAGE_SENT", "MESSAGE_OUTBOUND"}:
        event_type = "message_outbound"
    else:
        event_type = "status_update"

    direction = (content.get("direction") or details.get("direction") or "").upper()
    user_id = content.get("userId") or content.get("user_id")

    if direction == "FROM_HUB":
        actor = "human_agent" if user_id else "ai"
        human_handoff = bool(user_id)
        if event_type == "status_update":
            event_type = "message_outbound"
    else:
        actor = "customer"
        human_handoff = False
        if event_type == "status_update" and content.get("text"):
            event_type = "message_inbound"

    raw_type = (content.get("type") or "TEXT").upper()
    content_type = CONTENT_MAP.get(raw_type, "text")

    return NormalizedMessageEvent(
        event_type=event_type,
        external_message_id=str(content.get("id")) if content.get("id") else None,
        external_conversation_id=str(content.get("sessionId") or content.get("session_id") or "")
        or None,
        channel_to=details.get("to") or content.get("to"),
        contact_phone=details.get("from") or content.get("from"),
        content_type=content_type,  # type: ignore[arg-type]
        text=content.get("text"),
        timestamp=_parse_ts(content.get("timestamp")),
        actor=actor,  # type: ignore[arg-type]
        human_handoff_detected=human_handoff,
        raw_payload=payload,
        contact_name=details.get("pushName") or details.get("name"),
    )
