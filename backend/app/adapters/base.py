from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


EventType = Literal["message_inbound", "message_outbound", "status_update"]
ContentType = Literal["text", "image", "audio", "file", "location"]
Actor = Literal["customer", "ai", "human_agent", "system"]


@dataclass
class NormalizedMessageEvent:
    event_type: EventType
    external_message_id: str | None
    external_conversation_id: str | None
    channel_to: str | None
    contact_phone: str | None
    content_type: ContentType
    text: str | None
    timestamp: datetime | None
    actor: Actor
    human_handoff_detected: bool = False
    raw_payload: dict[str, Any] = field(default_factory=dict)
    contact_name: str | None = None
