"""Etiquetas automáticas, funil de vendas, resumo de transferência e perguntas
sem resposta.

Tudo aqui é determinístico (nenhuma chamada extra de LLM) e roda DEPOIS que a
resposta já foi enviada ao cliente — é leitura do que aconteceu, não parte do
atendimento. Quem chama envolve em try/except + savepoint: uma falha aqui
nunca pode impedir a IA de responder.

As funções de detecção de message_flow são importadas dentro das funções
(mesmo padrão de promotions.py), porque message_flow importa este módulo.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, KnowledgeGap, Lead, Message, Plan, Promotion
from app.services.promotions import promotion_mentioned_in_text, wants_promotion_inquiry
from app.services.text_normalize import normalize_text as _normalize_text
from app.services.text_normalize import normalize_tokens as _normalize_tokens

logger = logging.getLogger(__name__)

# Etapas "em andamento" do funil de vendas, em ordem. Fora delas: matriculado
# (ganho) e perdido.
SALES_FUNNEL = ("interessado", "qualificado", "proposta")
SALES_STAGE_WON = "matriculado"
SALES_STAGE_LOST = "perdido"
SALES_STAGES = (*SALES_FUNNEL, SALES_STAGE_WON, SALES_STAGE_LOST)
_STAGE_RANK = {None: 0, SALES_STAGE_LOST: 0, "interessado": 1, "qualificado": 2, "proposta": 3, SALES_STAGE_WON: 4}
_RANK_TO_STAGE = {1: "interessado", 2: "qualificado", 3: "proposta"}

# Categorias com um valor só por cliente: a etiqueta nova substitui a antiga.
_SINGLE_VALUE_CATEGORIES = {"unidade", "plano", "perfil"}

_TOPIC_TOKENS = {
    "financeiro": {
        "parcela", "parcelas", "boleto", "boletos", "atrasada", "atrasado", "atraso", "cobranca",
        "fatura", "estorno", "reembolso",
    },
    "informacoes": {
        "horario", "horarios", "funcionamento", "endereco", "localizacao", "estacionamento",
        "estrutura", "abre", "fecha",
    },
    "convidados": {"convidado", "convidados", "convite", "convites"},
    "app": {"aplicativo", "app"},
}
_PRICE_OBJECTION_TOKENS = {"caro", "salgado", "puxado", "desconto", "carissimo"}
_OBJECTION_PHRASES = {
    "preco": ("nao tenho condic", "sem condic", "fora do orcamento", "muito alto", "nao cabe no bolso"),
    "vou_pensar": (
        "vou pensar", "vou ver com", "depois eu vejo", "depois vejo", "te falo depois",
        "depois te falo", "vou analisar", "preciso pensar", "vou conversar com", "vou falar com",
    ),
    "tempo": ("sem tempo", "nao tenho tempo", "falta de tempo"),
    "distancia": ("longe", "distante", "fora de mao"),
}

TAG_LABELS = {
    "assunto:planos": "planos",
    "assunto:promocao": "promoção",
    "assunto:avaliacao_fisica": "avaliação física",
    "assunto:financeiro": "financeiro/parcelas",
    "assunto:cancelamento": "cancelamento",
    "assunto:tour": "tour",
    "assunto:informacoes": "informações da unidade",
    "assunto:convidados": "convidados",
    "assunto:app": "aplicativo",
    "objecao:preco": "preço",
    "objecao:fidelidade": "fidelidade",
    "objecao:vou_pensar": "vai pensar",
    "objecao:tempo": "falta de tempo",
    "objecao:distancia": "distância",
    "motivo:sem_resposta": "parou de responder",
}

# Frases com que a IA admite que não tinha a informação. A última é a
# introdução fixa da trava de diária/semanal (ver _fix_short_term_denial).
_GAP_PHRASES = (
    "nao tenho essa informacao",
    "nao tenho informacao",
    "nao tenho essa info",
    "nao encontrei essa informacao",
    "nao sei informar",
    "nao consigo informar",
    "nao tenho esse dado",
    "nao tenho acesso a essa",
)
_GAP_OUT_OF_CATALOG = "prefiro confirmar com a equipe"


def detect_tags(text: str, promotions: list[Promotion] | None = None) -> set[str]:
    """Etiquetas que UMA mensagem do cliente revela (assunto e objeção)."""
    from app.services.message_flow import (
        _is_physical_eval_intent,
        _message_mentions_cancellation,
        _text_has_plan_intent,
        _wants_gym_tour_explicit,
        _wants_no_fidelity_plan,
    )

    if not text or not text.strip():
        return set()
    tokens = _normalize_tokens(text)
    normalized = _normalize_text(text)
    tags: set[str] = set()

    if _text_has_plan_intent(text) or "quanto custa" in normalized or "quanto fica" in normalized:
        tags.add("assunto:planos")
    if wants_promotion_inquiry(text, promotions or []):
        tags.add("assunto:promocao")
    if _is_physical_eval_intent(text) or tokens & {"bioimpedancia", "avaliacao"}:
        tags.add("assunto:avaliacao_fisica")
    if _message_mentions_cancellation(text):
        tags.add("assunto:cancelamento")
    if _wants_gym_tour_explicit(text):
        tags.add("assunto:tour")
    for topic, topic_tokens in _TOPIC_TOKENS.items():
        if tokens & topic_tokens:
            tags.add(f"assunto:{topic}")

    if tokens & _PRICE_OBJECTION_TOKENS:
        tags.add("objecao:preco")
    for objection, phrases in _OBJECTION_PHRASES.items():
        if any(phrase in normalized for phrase in phrases):
            tags.add(f"objecao:{objection}")
    if _wants_no_fidelity_plan(text):
        tags.add("objecao:fidelidade")
    return tags


def merge_tags(existing: list[str] | None, new: set[str]) -> list[str]:
    """Junta etiquetas novas às do cliente. Categorias de valor único
    (unidade, plano, perfil) são substituídas; as demais acumulam."""
    replaced = {tag.split(":", 1)[0] for tag in new} & _SINGLE_VALUE_CATEGORIES
    merged = [t for t in (existing or []) if t.split(":", 1)[0] not in replaced]
    for tag in sorted(new):
        if tag not in merged:
            merged.append(tag)
    return merged


def advance_sales_stage(
    current: str | None, *, interested: bool, qualified: bool, proposal: bool
) -> str | None:
    """Próxima etapa do funil. Só avança (nunca volta), não mexe em quem já
    é "matriculado", e reativa quem estava "perdido" se houver sinal novo."""
    target = 3 if proposal else 2 if qualified else 1 if interested else 0
    if target == 0 or current == SALES_STAGE_WON:
        return current
    if current != SALES_STAGE_LOST and _STAGE_RANK.get(current, 0) >= target:
        return current
    return _RANK_TO_STAGE[target]


def detect_knowledge_gap(reply_text: str) -> str | None:
    """A IA não soube responder? Devolve o motivo, ou None."""
    normalized = _normalize_text(reply_text or "")
    if _GAP_OUT_OF_CATALOG in normalized:
        return "modalidade_fora_do_catalogo"
    if any(phrase in normalized for phrase in _GAP_PHRASES):
        return "sem_informacao"
    return None


async def _get_lead(db: AsyncSession, conversation: Conversation) -> Lead | None:
    from app.services.message_flow import sanitize_phone_digits

    phone = sanitize_phone_digits(conversation.contact_phone)
    if not phone:
        return None
    result = await db.execute(
        select(Lead).where(Lead.company_id == conversation.company_id, Lead.phone == phone)
    )
    return result.scalar_one_or_none()


def _is_student(lead: Lead | None) -> bool:
    return bool(lead and (lead.is_student or lead.unit))


async def update_lead_insights(
    db: AsyncSession,
    conversation: Conversation,
    customer_text: str,
    reply_text: str,
    *,
    recent_customer_texts: list[str] | None = None,
    recent_ai_texts: list[str] | None = None,
) -> None:
    """Depois de uma resposta enviada: etiqueta o cliente, avança o funil de
    vendas e registra pergunta sem resposta."""
    from app.services.message_flow import (
        _resolve_sales_art_unit,
        _says_not_student,
        _single_plan_mentioned,
        _upsert_lead,
    )
    from app.services.promotions import get_active_promotions

    promotions = await get_active_promotions(db, conversation.company_id)
    tags = detect_tags(customer_text, promotions)
    only_promotion = len(promotions) == 1
    for promotion in promotions:
        if promotion_mentioned_in_text(customer_text, promotion, only_promotion=only_promotion):
            tags.add(f"promo:{promotion.title}")

    lead = await _get_lead(db, conversation)
    student = _is_student(lead)
    if student:
        tags.add("perfil:aluno")
    elif _says_not_student(customer_text):
        tags.add("perfil:nao_aluno")

    # Unidade e plano: só interessa procurar quando a conversa é comercial
    # ou quando a resposta fala de valor.
    interested = bool({"assunto:planos", "assunto:promocao"} & tags) or any(
        t.startswith("promo:") for t in tags
    )
    talks_price = "r$" in (reply_text or "").lower()
    in_funnel = bool(lead and lead.sales_stage in SALES_FUNNEL)
    unit = None
    link_sent = False
    if interested or talks_price or in_funnel:
        unit = await _resolve_sales_art_unit(
            db,
            conversation.company_id,
            customer_text,
            reply_text or "",
            recent_customer_texts,
            recent_ai_texts or [],
            set(),
        )
        plans_result = await db.execute(
            select(Plan).where(Plan.company_id == conversation.company_id, Plan.is_active.is_(True))
        )
        all_plans = list(plans_result.scalars().all())
        sent_link_plans = [p for p in all_plans if p.signup_url and p.signup_url in (reply_text or "")]
        link_sent = bool(sent_link_plans)
        unit_plans = [p for p in all_plans if unit and p.unit_id == unit.id]
        chosen = _single_plan_mentioned(customer_text, unit_plans) if unit_plans else None
        if chosen is None and len({p.name for p in sent_link_plans if not unit or p.unit_id == unit.id}) == 1:
            chosen = next(p for p in sent_link_plans if not unit or p.unit_id == unit.id)
        if chosen:
            tags.add(f"plano:{chosen.name}")
    if lead and lead.unit:
        tags.add(f"unidade:{lead.unit}")
    elif unit:
        tags.add(f"unidade:{unit.name}")

    gap_reason = detect_knowledge_gap(reply_text or "")
    if gap_reason and conversation.channel != "test_console":
        db.add(
            KnowledgeGap(
                company_id=conversation.company_id,
                conversation_id=conversation.id,
                question=(customer_text or "").strip()[:1000],
                ai_reply=(reply_text or "").strip()[:1000],
                reason=gap_reason,
            )
        )

    if not tags:
        return
    if lead is None:
        # Só cria cadastro quando há algo a registrar — "oi" não vira lead.
        lead = await _upsert_lead(
            db, conversation.company_id, conversation.contact_phone, {}, stage_if_new="novo"
        )
        if lead is None:  # telefone sem dígitos (ex: Chat de teste)
            return
    lead.tags = merge_tags(lead.tags, tags)

    if not student:
        new_stage = advance_sales_stage(
            lead.sales_stage,
            interested=interested,
            qualified=bool(unit and talks_price and (interested or in_funnel)),
            proposal=link_sent,
        )
        if new_stage != lead.sales_stage:
            lead.sales_stage = new_stage
            lead.lost_reason = None
    await db.flush()


async def _recent_customer_texts(db: AsyncSession, conversation: Conversation, limit: int = 6) -> list[str]:
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    result = await db.execute(
        select(Message.text)
        .where(
            Message.conversation_id == conversation.id,
            Message.actor == "customer",
            Message.text.is_not(None),
            Message.created_at >= since,
        )
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    return [t for t in reversed(result.scalars().all()) if t and t.strip()]


def _values(tags: list[str], category: str) -> list[str]:
    prefix = f"{category}:"
    return [t[len(prefix):] for t in tags if t.startswith(prefix)]


async def build_handoff_summary(db: AsyncSession, conversation: Conversation, reason: str | None) -> str:
    """Resumo pro atendente que vai assumir: quem é, o que quer, o que já
    travou a decisão — pra ele não recomeçar a conversa do zero. Montado das
    etiquetas do cliente + as últimas mensagens dele (a mensagem que causou a
    transferência ainda não foi etiquetada nesse ponto)."""
    lead = await _get_lead(db, conversation)
    texts = await _recent_customer_texts(db, conversation)
    tags = list(lead.tags or []) if lead else []
    for text in texts:
        tags = merge_tags(tags, detect_tags(text))

    name = (lead.name if lead and lead.name else None) or conversation.contact_name or "sem nome"
    lines = [f"Cliente: {name} · {conversation.contact_phone}"]
    if _is_student(lead):
        lines.append(f"Perfil: aluno{f' da unidade {lead.unit}' if lead.unit else ''}")
    elif "perfil:nao_aluno" in tags:
        lines.append("Perfil: disse que ainda não é aluno")
    else:
        lines.append("Perfil: não identificado como aluno")

    topics = [TAG_LABELS.get(f"assunto:{v}", v) for v in _values(tags, "assunto")]
    if topics:
        lines.append("Assunto: " + ", ".join(topics))
    interest = []
    if not _is_student(lead) and _values(tags, "unidade"):
        interest.append("unidade " + _values(tags, "unidade")[0])
    if _values(tags, "plano"):
        interest.append("plano " + _values(tags, "plano")[0])
    interest.extend("promoção " + v for v in _values(tags, "promo"))
    if interest:
        lines.append("Interesse: " + " · ".join(interest))
    objections = [TAG_LABELS.get(f"objecao:{v}", v) for v in _values(tags, "objecao")]
    if objections:
        lines.append("Objeções: " + ", ".join(objections))
    if lead and lead.sales_stage:
        lines.append(f"Funil de vendas: {lead.sales_stage}")
    if reason:
        lines.append(f"Motivo da transferência: {reason.strip()}")
    if texts:
        quoted = " | ".join(f"“{t.strip()[:140]}”" for t in texts[-3:])
        lines.append(f"Últimas mensagens do cliente: {quoted}")
    return "\n".join(lines)


async def mark_sales_handoff(db: AsyncSession, conversation: Conversation) -> None:
    """Lead do funil passado pra um atendente fechar (tour, promoção,
    matrícula manual): conta como proposta em andamento."""
    lead = await _get_lead(db, conversation)
    if lead and not _is_student(lead) and lead.sales_stage in ("interessado", "qualificado"):
        lead.sales_stage = "proposta"
        await db.flush()


async def mark_lead_lost(db: AsyncSession, conversation: Conversation) -> None:
    """Conversa encerrada por falta de resposta: lead que estava interessado
    ou qualificado vira "perdido", com a última objeção como motivo. Quem já
    recebeu proposta (link/tour) fica como está — pode ter se matriculado
    pelo link sem responder, e isso a IA não tem como saber."""
    lead = await _get_lead(db, conversation)
    if not lead or lead.sales_stage not in ("interessado", "qualificado"):
        return
    objections = _values(lead.tags or [], "objecao")
    lead.sales_stage = SALES_STAGE_LOST
    lead.lost_reason = objections[-1] if objections else "sem_resposta"
    await db.flush()


async def compute_sales_insights(db: AsyncSession, company_id: UUID, days: int | None = None) -> dict:
    """Números do funil de vendas e das etiquetas, pro painel."""
    filters = [Lead.company_id == company_id, or_(Lead.sales_stage.is_not(None), func.jsonb_array_length(Lead.tags) > 0)]
    if days is not None:
        filters.append(Lead.updated_at >= datetime.now(timezone.utc) - timedelta(days=days))
    result = await db.execute(select(Lead).where(*filters))
    leads = list(result.scalars().all())

    stages = Counter(lead.sales_stage for lead in leads if lead.sales_stage)
    lost_reasons = Counter(lead.lost_reason for lead in leads if lead.sales_stage == SALES_STAGE_LOST and lead.lost_reason)
    by_category: dict[str, Counter] = {c: Counter() for c in ("assunto", "objecao", "unidade", "plano", "promo")}
    for lead in leads:
        for tag in lead.tags or []:
            category, _, value = tag.partition(":")
            if category in by_category and value:
                by_category[category][value] += 1

    def ranked(counter: Counter, *label_prefixes: str) -> list[dict]:
        def label(value: str) -> str:
            for prefix in label_prefixes:
                if f"{prefix}:{value}" in TAG_LABELS:
                    return TAG_LABELS[f"{prefix}:{value}"]
            return value

        return [{"value": value, "label": label(value), "count": count} for value, count in counter.most_common(12)]

    return {
        "stages": [{"stage": stage, "count": stages.get(stage, 0)} for stage in SALES_STAGES],
        "leads_in_funnel": sum(stages.values()),
        # Leads do funil que depois foram confirmados como alunos pelo CPF.
        # Não é conversão comprovada: pode ser aluno antigo perguntando de plano.
        "students_identified": sum(1 for lead in leads if lead.sales_stage and _is_student(lead)),
        "lost_reasons": ranked(lost_reasons, "objecao", "motivo"),
        "topics": ranked(by_category["assunto"], "assunto"),
        "objections": ranked(by_category["objecao"], "objecao"),
        "units": ranked(by_category["unidade"]),
        "plans": ranked(by_category["plano"]),
        "promotions": ranked(by_category["promo"]),
    }
