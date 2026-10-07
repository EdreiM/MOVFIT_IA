"""Assunto ativo e resumo compacto do histórico — reduz confusão do LLM em conversas longas."""
from __future__ import annotations

from app.models import Conversation, Lead, Message
from app.services.text_normalize import normalize_text as _normalize_text

# Acima disso, enviamos resumo + só a cauda recente pro modelo (mensagens completas).
_HISTORY_FULL_MAX = 18
_HISTORY_TAIL_KEEP = 12
_PREVIEW_MAX = 120
_SUMMARY_SNIPPET = 80


def trim_history_for_llm(history: list[Message]) -> list[Message]:
    if len(history) <= _HISTORY_FULL_MAX:
        return history
    return history[-_HISTORY_TAIL_KEEP:]


def infer_active_topic_label(
    *,
    user_text: str,
    recent_customer_texts: list[str] | None,
    history: list[Message] | None,
    cancellation_active: bool,
    app_support_active: bool,
    physical_eval_context: bool,
    plan_intent_active: bool,
    tour_discussed: bool,
    tour_explicit: bool,
    student_operational: bool,
    promotion_transfer: bool,
) -> str:
    if cancellation_active:
        return "cancelamento ou trancamento de matrícula"
    if app_support_active:
        return "problema ou agendamento no aplicativo (bioimpedância / treino bloqueado)"
    if physical_eval_context:
        return "agendamento de avaliação física ou bioimpedância"
    if plan_intent_active:
        return "planos, preços ou matrícula"
    if tour_explicit or tour_discussed:
        return "tour ou visita à academia (não é avaliação física)"
    if promotion_transfer:
        return "promoção ou campanha com atendente"
    if student_operational:
        return "assunto de aluno matriculado (parcela, convidados, acesso etc.)"
    normalized = _normalize_text(user_text or "")
    if any(w in normalized for w in ("horario", "funcionamento", "abre", "fecha")):
        return "horário de funcionamento ou estrutura da unidade"
    if any(w in normalized for w in ("endereco", "estacionamento", "onde fica")):
        return "localização / endereço da unidade"
    prior = " ".join(_normalize_text(t) for t in (recent_customer_texts or [])[-4:] if t)
    if "plano" in prior or "preco" in prior or "mensalidade" in prior:
        return "planos ou valores (contexto recente)"
    if "bioimped" in prior or "avaliacao fisica" in prior:
        return "avaliação física ou bioimpedância (contexto recente)"
    return "dúvida geral sobre a academia"


def build_active_topic_system_block(topic_label: str) -> str:
    return (
        "[Assunto ativo nesta conversa]\n"
        f"Prioridade agora: *{topic_label}*. "
        "Mantenha foco nisso até o cliente mudar claramente de assunto. "
        "Não misture tour com avaliação física, nem planos com cancelamento. "
        "Confirmações curtas (\"sim\", nome de cidade) referem-se a este assunto, "
        "não a ofertas antigas de tour no histórico."
    )


def build_history_summary_system_block(
    history: list[Message],
    lead: Lead | None,
    conversation: Conversation,
) -> str | None:
    if len(history) < 8:
        return None

    facts: list[str] = []
    if lead:
        if lead.unit:
            facts.append(f"Aluno confirmado na unidade: {lead.unit}.")
        elif lead.cpf:
            facts.append("CPF já informado; unidade de aluno ainda não confirmada no cadastro.")
        if lead.name:
            first = (lead.name or "").split()[0]
            if first:
                facts.append(f"Nome no cadastro: {first}.")

    plans_sent = any(
        (m.raw_payload or {}).get("plan_caption") or (m.raw_payload or {}).get("images")
        for m in history
        if m.actor == "ai"
    )
    if plans_sent:
        facts.append("Imagens/descrições de planos já foram enviadas nesta conversa.")

    offered = conversation.physical_eval_offered_slots or {}
    if offered.get("slots"):
        facts.append(
            f"Lista numerada de horários de avaliação já mostrada"
            f"{(' (data ' + str(offered.get('date')) + ')') if offered.get('date') else ''}."
        )

    if conversation.status == "with_human":
        facts.append("Status: conversa com atendente humano (IA só se reativada).")

    older = history[:-6] if len(history) > 6 else history[:-2]
    customer_older: list[str] = []
    for m in older:
        if m.actor != "customer" or not m.text:
            continue
        snippet = m.text.strip().replace("\n", " ")
        if len(snippet) > _SUMMARY_SNIPPET:
            snippet = snippet[: _SUMMARY_SNIPPET - 1] + "…"
        customer_older.append(snippet)
    if customer_older:
        facts.append(
            "Pedidos anteriores do cliente (trecho): "
            + " | ".join(customer_older[-4:])
        )

    if not facts:
        return None

    return (
        "[Resumo do início da conversa — use como memória; mensagens recentes vêm abaixo]\n"
        + "\n".join(f"• {line}" for line in facts)
    )


def message_preview_text(text: str | None, *, max_len: int = _PREVIEW_MAX) -> str | None:
    if not text or not text.strip():
        return None
    one_line = " ".join(text.split())
    if len(one_line) <= max_len:
        return one_line
    return one_line[: max_len - 1] + "…"
