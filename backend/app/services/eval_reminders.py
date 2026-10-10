"""Lembrete da véspera da avaliação física agendada pela IA.

Roda dentro da varredura de follow-up (`run_followup_sweep`), que já segura o
advisory lock — por isso não tem namespace próprio. A trava de duplicidade é
`Lead.physical_eval_reminder_for` (data da avaliação já lembrada), gravada e
commitada logo depois de cada envio. Avaliação remarcada tem data nova, então
ganha o próprio lembrete."""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.adapters.base import NormalizedMessageEvent
from app.config import get_settings
from app.models import Conversation, Lead
from app.services.message_flow import (
    _format_schedule_date_br,
    save_message,
    send_outbound,
)

logger = logging.getLogger(__name__)

_BRAZIL_TZ = timezone(timedelta(hours=-3))
_LAST_REMINDER_HOUR = 21  # depois disso não manda mais — vira incômodo, não lembrete


def build_reminder_text(lead: Lead) -> str:
    first_name = (lead.name or "").strip().split()[0:1]
    greeting = f"Oi, {first_name[0]}! " if first_name else "Oi! "
    day = _format_schedule_date_br(lead.last_physical_eval_date or "")[:5]
    unit = f" na unidade {lead.unit}" if lead.unit else ""
    return (
        f"{greeting}Passando pra lembrar da sua *avaliação física* amanhã ({day}) às "
        f"{lead.last_physical_eval_time}{unit}. 😊\n\n"
        "Se não puder ir, é só me responder *remarcar* que eu te ajudo a escolher outro horário."
    )


async def _latest_conversation(db, lead: Lead) -> Conversation | None:
    digits = func.regexp_replace(Conversation.contact_phone, r"\D", "", "g")
    result = await db.execute(
        select(Conversation)
        .where(
            Conversation.company_id == lead.company_id,
            Conversation.channel != "test_console",
            digits == lead.phone,
        )
        .order_by(Conversation.updated_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def run_eval_reminders(db, now: datetime | None = None) -> int:
    """Manda o lembrete de amanhã. Retorna quantos foram enviados."""
    local = (now or datetime.now(timezone.utc)).astimezone(_BRAZIL_TZ)
    if not (get_settings().eval_reminder_hour <= local.hour <= _LAST_REMINDER_HOUR):
        return 0

    tomorrow = (local.date() + timedelta(days=1)).strftime("%Y%m%d")
    leads = (
        (
            await db.execute(
                select(Lead).where(
                    Lead.physical_eval_scheduled.is_(True),
                    Lead.last_physical_eval_date == tomorrow,
                    Lead.last_physical_eval_time.is_not(None),
                    (Lead.physical_eval_reminder_for.is_(None))
                    | (Lead.physical_eval_reminder_for != tomorrow),
                )
            )
        )
        .scalars()
        .all()
    )

    sent = 0
    for lead in leads:
        try:
            conversation = await _latest_conversation(db, lead)
            # Sem conversa pra responder (ou humano no meio do atendimento):
            # não manda nem marca — se o humano devolver a conversa ainda hoje,
            # o próximo ciclo manda.
            if not conversation or conversation.status == "with_human":
                continue
            text = build_reminder_text(lead)
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
                    text=text,
                    timestamp=datetime.now(timezone.utc),
                    actor="ai",
                    raw_payload={"is_eval_reminder": True, "eval_date": tomorrow},
                ),
            )
            await send_outbound(db, conversation.company_id, conversation, text)
            lead.physical_eval_reminder_for = tomorrow
            await db.commit()
            sent += 1
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao mandar lembrete de avaliação (lead %s)", lead.id)
            await db.rollback()
    if sent:
        logger.info("Lembretes de avaliação física enviados: %s", sent)
    return sent
