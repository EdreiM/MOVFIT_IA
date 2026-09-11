from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.adapters.base import NormalizedMessageEvent
from app.config import get_settings
from app.models import (
    AiConfig,
    Conversation,
    Integration,
    Lead,
    Message,
    MetricsDaily,
    Number,
    Plan,
    RagSource,
    Tool,
    ToolCallLog,
    Unit,
    WebhookLog,
)
from app.security import decrypt_secret
from app.services.debounce import schedule_ai_reply
from app.services.llm import chat_completion

logger = logging.getLogger(__name__)


def sanitize_phone_digits(phone: str | None) -> str:
    """Normaliza telefone pra só dígitos — formato único independente da
    plataforma/adaptador de origem, pra quem consome (Evolution outbound,
    contexto de ferramentas) não precisar tratar formato por conta própria."""
    if not phone:
        return ""
    return "".join(ch for ch in phone if ch.isdigit())


def split_into_bubbles(text: str, max_chars: int) -> list[str]:
    """Quebra uma resposta longa em "bolhas" menores, como alguém mandando
    várias mensagens seguidas no WhatsApp em vez de um texto único enorme.

    Regra: parágrafos (separados por linha em branco) viram bolhas próprias
    quando cabem no limite. Um parágrafo com quebras de linha internas (ex:
    lista numerada de planos) é mantido inteiro — dividir uma lista no meio
    fica pior do que uma bolha um pouco mais longa. Só prosa corrida muito
    longa é quebrada, e aí por frase, empacotando até o limite.
    """
    text = text.strip()
    if not text:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    hard_limit = max_chars * 2
    bubbles: list[str] = []

    for para in paragraphs:
        if len(para) <= max_chars or ("\n" in para and len(para) <= hard_limit):
            bubbles.append(para)
            continue

        sentences = re.split(r"(?<=[.!?])\s+", para)
        chunk = ""
        for sentence in sentences:
            candidate = f"{chunk} {sentence}".strip() if chunk else sentence
            if len(candidate) <= max_chars:
                chunk = candidate
            else:
                if chunk:
                    bubbles.append(chunk)
                chunk = sentence
        if chunk:
            bubbles.append(chunk)

    return bubbles or [text]


async def get_or_create_conversation(
    db: AsyncSession,
    company_id: UUID,
    event: NormalizedMessageEvent,
    number: Number | None = None,
    integration_id: UUID | None = None,
) -> Conversation:
    conv: Conversation | None = None
    if event.external_conversation_id:
        result = await db.execute(
            select(Conversation).where(
                Conversation.company_id == company_id,
                Conversation.external_conversation_id == event.external_conversation_id,
            )
        )
        conv = result.scalar_one_or_none()

    if not conv and event.contact_phone:
        result = await db.execute(
            select(Conversation)
            .where(
                Conversation.company_id == company_id,
                Conversation.contact_phone == event.contact_phone,
                Conversation.status != "resolved",
            )
            .order_by(Conversation.created_at.desc())
            .limit(1)
        )
        conv = result.scalar_one_or_none()

    if not conv:
        conv = Conversation(
            company_id=company_id,
            number_id=number.id if number else None,
            integration_id=integration_id,
            external_conversation_id=event.external_conversation_id,
            contact_phone=event.contact_phone or "unknown",
            contact_name=event.contact_name,
            channel=number.channel_type if number else "webhook",
            ai_enabled=True,
            status="open",
            last_message_at=event.timestamp or datetime.now(timezone.utc),
        )
        db.add(conv)
        await db.flush()
    else:
        if event.contact_name and not conv.contact_name:
            conv.contact_name = event.contact_name
        if event.external_conversation_id and not conv.external_conversation_id:
            conv.external_conversation_id = event.external_conversation_id
        if integration_id and not conv.integration_id:
            conv.integration_id = integration_id
        conv.last_message_at = event.timestamp or datetime.now(timezone.utc)

    if event.human_handoff_detected:
        conv.ai_enabled = False
        conv.status = "with_human"

    return conv


async def save_message(
    db: AsyncSession,
    conversation: Conversation,
    event: NormalizedMessageEvent,
) -> Message:
    direction = "inbound" if event.event_type == "message_inbound" else "outbound"
    if event.actor == "customer":
        direction = "inbound"
    elif event.actor in {"ai", "human_agent", "system"}:
        direction = "outbound"

    msg = Message(
        conversation_id=conversation.id,
        company_id=conversation.company_id,
        external_message_id=event.external_message_id,
        direction=direction,
        actor=event.actor,
        content_type=event.content_type,
        text=event.text,
        raw_payload=event.raw_payload,
        # Timestamp explícito em Python: o server_default now() do Postgres é
        # fixo por transação, então mensagens salvas em sequência na mesma
        # transação (ex: bolhas de uma resposta) ficariam todas com o mesmo
        # created_at e a ordem de exibição deixaria de ser garantida.
        created_at=event.timestamp or datetime.now(timezone.utc),
    )
    db.add(msg)
    await db.flush()
    if conversation.channel != "test_console":
        await bump_metrics(db, conversation.company_id, direction)
    return msg


async def bump_metrics(db: AsyncSession, company_id: UUID, direction: str) -> None:
    today = date.today()
    result = await db.execute(
        select(MetricsDaily).where(MetricsDaily.company_id == company_id, MetricsDaily.day == today)
    )
    row = result.scalar_one_or_none()
    if not row:
        row = MetricsDaily(company_id=company_id, day=today)
        db.add(row)
        await db.flush()
    if direction == "inbound":
        row.messages_inbound += 1
    else:
        row.messages_outbound += 1


def _format_price(value: float) -> str:
    return f"R$ {value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


async def build_catalog_context(db: AsyncSession, company_id: UUID) -> str:
    """Monta o catálogo de unidades e planos direto do banco (fonte oficial,
    sempre atualizada) — substitui a consulta à RAG de planos."""
    result = await db.execute(
        select(Unit)
        .options(selectinload(Unit.plans))
        .where(Unit.company_id == company_id, Unit.is_active.is_(True))
        .order_by(Unit.city, Unit.name)
    )
    units = result.scalars().unique().all()

    blocks: list[str] = []
    for unit in units:
        active_plans = [p for p in unit.plans if p.is_active]
        if not active_plans:
            continue
        type_suffix = f" — {unit.unit_type}" if unit.unit_type else ""
        lines = [f"### {unit.name} ({unit.city}{type_suffix})"]
        for p in active_plans:
            line = f"- {p.name}: {_format_price(p.monthly_price)}/mês"
            if p.fidelity_months:
                line += f", fidelidade {p.fidelity_months} meses"
            if p.enrollment_fee:
                line += f", matrícula {_format_price(p.enrollment_fee)}"
            if p.payment_info:
                line += f" ({p.payment_info})"
            lines.append(line)
            if p.benefits:
                lines.append("  Benefícios: " + "; ".join(p.benefits))
            if p.signup_url:
                lines.append(f"  Link de cadastro: {p.signup_url}")
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def _format_plan_caption(unit: Unit, plan: Plan) -> str:
    """Legenda pronta pra WhatsApp (negrito com um asterisco, emojis, sem
    markdown de título) pra ir junto da imagem do plano — pensada pra virar
    o caption da própria mensagem de mídia no n8n, então imagem e descrição
    chegam como uma coisa só, na ordem certa, sem depender de sincronizar
    dois envios separados (imagem via webhook da ferramenta, texto via
    outbound). Mesmo template que a IA usa quando escreve a descrição em
    texto livre — ver instrução de formatação em generate_ai_reply."""
    lines = [f"🏋️ *{plan.name.upper()}*", "", f"💰 *{_format_price(plan.monthly_price)} por mês*"]
    if plan.payment_info:
        lines.append(f"💳 Pagamento em {plan.payment_info}")
    if plan.fidelity_months:
        lines.append(f"📅 Fidelidade de {plan.fidelity_months} meses")
    if plan.enrollment_fee:
        lines.append(f"🎟️ Taxa de inscrição: {_format_price(plan.enrollment_fee)}")
    if plan.benefits:
        lines.append("")
        lines.append("✅ *Você terá:*")
        lines.extend(f"• {b}" for b in plan.benefits)
    if plan.signup_url:
        lines.append("")
        lines.append("👉 *Faça sua matrícula pelo link:*")
        lines.append(plan.signup_url)
    return "\n".join(lines)


async def fetch_rag_context(db: AsyncSession, company_id: UUID, query: str) -> str:
    result = await db.execute(
        select(RagSource).where(RagSource.company_id == company_id, RagSource.is_active.is_(True))
    )
    sources = result.scalars().all()
    chunks: list[str] = []
    # Formato do webhook n8n Mov Fit
    # - pergunta: texto usado na busca semântica
    # - contexto.plano_em_negociacao: opcional; só preencher quando soubermos o plano
    payload = {
        "pergunta": query.strip(),
        "contexto": {
            "plano_em_negociacao": None,
        },
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        for source in sources:
            try:
                started = datetime.now(timezone.utc)
                resp = await client.post(source.webhook_url, json=payload)
                latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
                source.last_latency_ms = latency
                if resp.status_code < 400:
                    source.last_success_at = datetime.now(timezone.utc)
                    data = resp.json()
                    text = _extract_rag_text(data)
                    if text:
                        chunks.append(f"[{source.name}]\n{text}")
            except Exception as exc:  # noqa: BLE001
                logger.warning("RAG %s falhou: %s", source.name, exc)
    return "\n\n".join(chunks)


def _extract_rag_text(data: dict | list | str) -> str:
    """Aceita o formato do n8n Mov Fit e aliases comuns."""
    if isinstance(data, str):
        return data
    if isinstance(data, list):
        return "\n".join(_extract_rag_text(item) for item in data if item)
    if not isinstance(data, dict):
        return str(data)

    # Formato Organiza do workflow WEBHOOK DE RAG
    if data.get("resposta"):
        parts = [str(data["resposta"])]
        extra = data.get("chunks") or []
        for c in extra[1:4]:
            if isinstance(c, dict) and c.get("conteudo"):
                titulo = c.get("titulo") or "FAQ"
                parts.append(f"- {titulo}: {c['conteudo']}")
            elif isinstance(c, str):
                parts.append(c)
        return "\n".join(parts)

    for key in ("answer", "context", "text", "content"):
        if data.get(key):
            return str(data[key])

    if data.get("chunks"):
        lines = []
        for c in data["chunks"]:
            if isinstance(c, dict):
                titulo = c.get("titulo") or c.get("title") or ""
                conteudo = c.get("conteudo") or c.get("content") or ""
                if conteudo:
                    lines.append(f"{titulo}: {conteudo}".strip(": "))
            else:
                lines.append(str(c))
        return "\n".join(lines)

    return ""


# tool_key com efeito interno além de chamar o webhook: transferir tira a IA
# do controle da conversa, encerrar marca como resolvida, enviar imagens
# recebe as URLs reais do catálogo (a IA não precisa "lembrar" o link de
# cor). Qualquer outro tool_key (consultar aluno, o que mais surgir) é 100%
# genérico — o webhook faz o trabalho real e só devolve o resultado pra IA.
TOOL_KEY_TRANSFER = "transferir_atendimento"
TOOL_KEY_END = "encerrar_atendimento"
TOOL_KEY_SEND_PLAN_IMAGES = "enviar_imagens_planos"
# Descobre em qual unidade o CPF está matriculado (cascade Pacto/n8n).
# Efeito interno: com sucesso + dados.unidade, grava unidade no Lead e
# marca is_student — pra próximas tools (parcela, convidados etc.) não
# precisarem perguntar a unidade de novo. NÃO usar pra quem só pede planos.
TOOL_KEY_VERIFY_UNIT_BY_CPF = "verificar_unidade_por_cpf"
# Opcional — só existe pra empresas cuja plataforma (ex: WTS/GYMBOT) permite
# consultar se a sessão do cliente ainda está pendente. Usada só
# internamente pelo follow-up (app/services/followup.py) antes de mandar
# qualquer mensagem, pra não reengajar um atendimento que já foi concluído
# por fora da Mônica (manualmente, ou por outro motivo). NUNCA é exposta
# como função chamável pela IA — ver exclusão em tool_defs abaixo.
TOOL_KEY_CHECK_SESSION = "verificar_sessao_atendimento"

# Ferramenta interna, sempre disponível pra qualquer empresa — não é um
# webhook n8n, é tratada 100% dentro do próprio código (grava no cadastro
# estruturado do cliente, ver models.Lead). Existe pra a IA não depender só
# da janela de histórico de mensagens pra "lembrar" nome, CPF, e-mail etc.
TOOL_KEY_SAVE_LEAD_DATA = "salvar_dado_cliente"

SAVE_LEAD_DATA_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": TOOL_KEY_SAVE_LEAD_DATA,
        "description": (
            "Salva/atualiza o cadastro do cliente com dados que ele informou na "
            "conversa (nome, CPF, e-mail, data de nascimento) e/ou o estágio dele "
            "no funil (ex: qualificado, interessado, sem interesse). Chame isso "
            "assim que o cliente disser um desses dados pela primeira vez, ou "
            "corrigir um valor — depois de salvo, não precisa perguntar de novo. "
            "IMPORTANTE: chame isso MESMO QUANDO o dado for informado só pra "
            "outra ferramenta (ex: o cliente deu o CPF pra consultar parcela) — "
            "chame as duas ferramentas, essa aqui pra guardar o dado permanente e "
            "a outra pra resolver o pedido dele. Não invente nenhum valor; só "
            "passe o que o cliente realmente disse, e só os campos que mudaram."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "nome": {"type": "string", "description": "Nome completo do cliente"},
                "cpf": {"type": "string", "description": "CPF do cliente, só números ou formatado"},
                "email": {"type": "string", "description": "E-mail do cliente"},
                "data_nascimento": {
                    "type": "string",
                    "description": "Data de nascimento do cliente, no formato AAAA-MM-DD",
                },
                "unidade": {
                    "type": "string",
                    "description": "Unidade onde o cliente já é aluno confirmado (não a que ele só perguntou de passagem)",
                },
                "estagio": {
                    "type": "string",
                    "description": (
                        "Estágio do cliente no funil de atendimento, texto livre "
                        "curto (ex: qualificado, interessado, sem interesse). Não "
                        "use isso pra marcar transferência ou encerramento — essas "
                        "ferramentas já atualizam o estágio sozinhas."
                    ),
                },
            },
            "required": [],
        },
    },
}


# Nomes de argumento que, se aparecerem em QUALQUER chamada de ferramenta
# (não só salvar_dado_cliente), já valem pra atualizar o cadastro do cliente
# — cobre o caso comum de "cliente deu o CPF pra outra ferramenta usar".
_LEAD_ARG_KEYS = {"cpf", "nome", "email", "data_nascimento"}


async def _upsert_lead(
    db: AsyncSession,
    company_id: UUID,
    phone: str,
    fields: dict,
    stage_if_new: str | None = None,
    sticky_flags: dict[str, bool] | None = None,
) -> Lead | None:
    """Cria ou atualiza o cadastro do cliente (por telefone dentro da
    empresa) com os campos informados — só sobrescreve o que vier
    preenchido, o resto do cadastro existente fica como estava.

    `stage_if_new` só é aplicado se não vier "estagio" explícito em `fields`
    E o estágio atual ainda for o padrão "novo" — usado pra sinalizar "isso
    aqui prova que o cliente já é aluno" sem sobrescrever um estágio mais
    avançado que já tenha sido definido (ex: transferido, resolvido).

    `sticky_flags` (ex: {"is_student": True}) só liga — nunca desliga um
    flag que já esteja True. Usado pra métrica que não pode se perder
    quando o `stage` (um valor só) muda depois pra outra coisa (ex: aluno
    que depois é transferido continua contando como aluno)."""
    phone_digits = sanitize_phone_digits(phone)
    if not phone_digits:
        # Conversa sem telefone real (ex: placeholder do Chat de teste) —
        # não cria lead fantasma, sem como identificar o cliente de verdade.
        return None
    result = await db.execute(select(Lead).where(Lead.company_id == company_id, Lead.phone == phone_digits))
    lead = result.scalar_one_or_none()
    if not lead:
        # stage="novo" explícito (não só confiar no default da coluna): o
        # default só é aplicado pelo SQLAlchemy no flush, então checar
        # `lead.stage == "novo"` mais abaixo (stage_if_new) falharia pra um
        # lead criado nesta mesma chamada — lead.stage ainda seria None em
        # memória até flush.
        lead = Lead(company_id=company_id, phone=phone_digits, stage="novo")
        db.add(lead)
    if fields.get("nome"):
        lead.name = str(fields["nome"])
    if fields.get("cpf"):
        lead.cpf = str(fields["cpf"])
    if fields.get("email"):
        lead.email = str(fields["email"])
    if fields.get("data_nascimento"):
        try:
            lead.birthdate = date.fromisoformat(str(fields["data_nascimento"]))
        except ValueError:
            pass
    if fields.get("unidade"):
        lead.unit = str(fields["unidade"])
    if fields.get("estagio"):
        lead.stage = str(fields["estagio"])
    elif stage_if_new and lead.stage == "novo":
        lead.stage = stage_if_new
    for flag_name, value in (sticky_flags or {}).items():
        if value:
            setattr(lead, flag_name, True)
    await db.flush()
    return lead


def _normalize_tokens(text: str) -> set[str]:
    """Minúsculo, sem acento, só letras/números — pra comparar 'Santarém' com
    'santarem' e 'MOVFIT Santarém — Premium (24h)' com 'Santarém - 24 horas'
    mesmo quando a IA parafraseia em vez de copiar o nome literal."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return set(re.findall(r"[a-z0-9]+", ascii_text.lower()))


def _normalize_text(text: str) -> str:
    """Como _normalize_tokens, mas preserva a ordem/espaços — pra procurar
    frase inteira (ex: 'vou encaminhar'), não só palavras soltas."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", ascii_text.lower())


def _mentions_cancellation(text: str) -> bool:
    """Detecta "cancelar"/"cancelamento"/"cancelando" etc no motivo que a
    IA manda ao transferir — só pra métrica, não muda nenhum comportamento
    da conversa."""
    return any(token.startswith("cancel") for token in _normalize_tokens(text))


def _best_matches(candidates: list[tuple[str, set[str]]], arg_tokens: set[str], threshold: float) -> list[str]:
    """Pontua cada candidato pela fração dos seus tokens de identidade que
    aparecem nos argumentos da IA (recall) — não exige nome idêntico, só que
    o suficiente dele apareça. Retorna todo mundo empatado no topo — quem
    chama decide o que fazer com empate (ver `_unique_match`)."""
    scored = [(name, len(tokens & arg_tokens) / len(tokens)) for name, tokens in candidates if tokens]
    scored = [(name, score) for name, score in scored if score >= threshold]
    if not scored:
        return []
    best_score = max(score for _, score in scored)
    return [name for name, score in scored if score == best_score]


def _unique_match(candidates: list[tuple[str, set[str]]], arg_tokens: set[str], threshold: float) -> str | None:
    """Como `_best_matches`, mas só devolve um resultado quando ele é
    inequívoco — empate no topo (duas unidades igualmente prováveis) devolve
    None, porque errar a unidade é pior do que admitir que não achou."""
    matches = _best_matches(candidates, arg_tokens, threshold)
    return matches[0] if len(matches) == 1 else None


async def _resolve_plan_images(db: AsyncSession, company_id: UUID, arguments: dict) -> list[dict]:
    """Cruza os argumentos que a IA extraiu (nome da unidade — e, se
    configurado, nome do plano — em qualquer parâmetro que o usuário tenha
    cadastrado) com o catálogo real, e devolve as URLs de imagem certas — em
    vez de confiar que a IA reproduza o link de memória.

    A IA nem sempre repete o nome cadastrado ao pé da letra (já vimos ela
    mandar "MOVFIT Santarém — Premium (24h)" pra uma unidade cadastrada como
    "Santarém - 24 horas"), então o match é por sobreposição de tokens
    (nome + cidade + tipo da unidade) em vez de substring exata. Ambiguidade
    (duas unidades empatadas no topo) não escolhe nenhuma — melhor a IA
    avisar que não achou do que mandar imagem da unidade errada."""
    result = await db.execute(
        select(Unit)
        .options(selectinload(Unit.plans))
        .where(Unit.company_id == company_id, Unit.is_active.is_(True))
    )
    units = result.scalars().unique().all()

    arg_tokens = _normalize_tokens(" ".join(str(v) for v in arguments.values() if v))
    if not arg_tokens:
        return []

    if len(units) == 1:
        # Só uma unidade ativa — não tem ambiguidade nenhuma pra escolher a
        # errada, então não exige que a IA repita nome+cidade+tipo por
        # completo (ela às vezes só manda a cidade, tipo "Santarém", e isso
        # já basta). Ainda assim exige alguma menção real — argumento sem
        # nenhuma palavra em comum não deve virar match "porque só tem uma".
        only_unit = units[0]
        unit_tokens = _normalize_tokens(f"{only_unit.name} {only_unit.city} {only_unit.unit_type or ''}")
        matched_units = [only_unit] if unit_tokens & arg_tokens else []
    else:
        unit_candidates = [
            (str(u.id), _normalize_tokens(f"{u.name} {u.city} {u.unit_type or ''}")) for u in units
        ]
        matched_unit_id = _unique_match(unit_candidates, arg_tokens, threshold=0.4)
        matched_units = [u for u in units if str(u.id) == matched_unit_id] if matched_unit_id else []

    images: list[dict] = []
    for unit in matched_units:
        available = [p for p in unit.plans if p.is_active and p.image_url]
        plan_candidates = [(str(p.id), _normalize_tokens(p.name)) for p in available]
        matched_plan_id = _unique_match(plan_candidates, arg_tokens, threshold=0.4)
        matched_plans = [p for p in available if str(p.id) == matched_plan_id] if matched_plan_id else []
        for plan in matched_plans or available:
            images.append(
                {
                    "unidade": unit.name,
                    "plano": plan.name,
                    "url": plan.image_url,
                    "legenda": _format_plan_caption(unit, plan),
                }
            )
    return images


async def _get_sent_plan_image_urls(db: AsyncSession, conversation_id: UUID) -> set[str]:
    """URLs de imagem de plano já mandadas nesta conversa — pra não repetir a
    mesma imagem toda vez que o assunto voltar à tona (só reenvia se o
    cliente pedir explicitamente de novo)."""
    result = await db.execute(
        select(Message.raw_payload).where(
            Message.conversation_id == conversation_id,
            Message.content_type == "image",
        )
    )
    urls: set[str] = set()
    for payload in result.scalars().all():
        for img in (payload or {}).get("images", []):
            url = img.get("url")
            if url:
                urls.add(url)
    return urls


_IMAGE_REQUEST_KEYWORDS = {"imagem", "imagens", "foto", "fotos"}


def _wants_image_explicitly(text: str) -> bool:
    return bool(_normalize_tokens(text) & _IMAGE_REQUEST_KEYWORDS)


_PLAN_INTENT_KEYWORDS = {
    "plano", "planos", "preco", "precos", "valor", "valores", "mensalidade",
    "matricula", "matricular", "assinar", "assinatura", "contratar", "contrato",
}

# Assuntos que encerram o fluxo de planos — mesmo que o cliente tenha pedido
# planos antes, a mensagem ATUAL muda de assunto (horário, parcela etc.).
_NON_PLAN_TOPIC_KEYWORDS = {
    "horario", "horarios", "funcionamento", "abre", "aberta", "aberto", "fecha",
    "endereco", "localizacao", "onde", "fica", "estacionamento", "estrutura",
    "parcela", "parcelas", "atrasad", "atraso", "inadimpl", "boleto", "boletos",
    "convidado", "convidados", "convite", "convites", "acesso", "entrada",
    "cancelar", "cancelamento", "trancar", "trancamento", "congelar",
    "carne", "carnê", "multa", "pagar", "pagamento",
}

_STUDENT_ACTION_KEYWORDS = {
    "parcela", "parcelas", "atrasad", "atraso", "inadimpl", "boleto", "boletos",
    "convidado", "convidados", "convite", "convites", "acesso", "entrada",
    "carne", "carnê", "multa", "minha", "matricula", "matriculado", "aluno",
}


def _text_has_plan_intent(text: str) -> bool:
    return bool(_normalize_tokens(text) & _PLAN_INTENT_KEYWORDS)


def _text_has_non_plan_topic(text: str) -> bool:
    return bool(_normalize_tokens(text) & _NON_PLAN_TOPIC_KEYWORDS)


def _is_guest_info_question(text: str) -> bool:
    """Dúvida informativa sobre convidados — RAG/explicação, não ferramenta ainda."""
    tokens = _normalize_tokens(text)
    if not tokens & {"convidado", "convidados", "convite", "convites"}:
        return False
    # "quantos convidados posso levar?" é operacional (consultar limite no sistema).
    if tokens & {"quantos", "quantas", "restante", "restantes", "limite", "saldo", "usei", "utilizei"}:
        return False
    return True


def _student_operational_action(text: str) -> bool:
    """Ação de aluno que exige CPF/unidade/ferramenta — só a mensagem ATUAL."""
    if not text or _is_guest_info_question(text):
        return False
    tokens = _normalize_tokens(text)
    if tokens & {"parcela", "parcelas", "atrasad", "atraso", "inadimpl", "boleto", "boletos"}:
        return True
    if tokens & {"convidado", "convidados", "convite", "convites"}:
        return True
    if "matricula" in tokens or "matriculado" in tokens or "aluno" in tokens:
        if tokens & {"minha", "meu", "minhas", "meus", "atrasad", "atraso", "cancelar", "pagar"}:
            return True
    return False


def _wants_student_action(text: str, recent_customer_texts: list[str] | None = None) -> bool:
    """Alias mantido pros testes — olha só a mensagem atual."""
    return _student_operational_action(text)


def _plan_flow_active(text: str, recent_customer_texts: list[str] | None = None) -> bool:
    """Verdadeiro enquanto o cliente ainda está no fluxo de planos/preços.

    Turno 1 "quero planos" + turno 2 "Santarém - 24 horas" → True.
    Depois "quais os horários?" → False, mesmo tendo pedido planos antes."""
    if _text_has_plan_intent(text):
        return True
    if _text_has_non_plan_topic(text):
        return False
    if not recent_customer_texts:
        return False
    prior = [t for t in recent_customer_texts if t and t.strip() != (text or "").strip()]
    return any(_text_has_plan_intent(t) for t in prior)


def _wants_plan_info(text: str, recent_customer_texts: list[str] | None = None) -> bool:
    """Alias de _plan_flow_active — rede de segurança de envio de imagens."""
    return _plan_flow_active(text, recent_customer_texts)


_TRANSFER_PROMISE_PHRASES = [
    "vou encaminhar", "vou te transferir", "vou transferir voce",
    "vou chamar um atendente", "vou passar para um atendente", "vou passar pra um atendente",
    "encaminhar para o atendimento", "encaminhar para um atendente", "encaminhando para um atendente",
    "transferir para um atendente", "transferindo para um atendente", "um atendente vai te",
    "um atendente ira", "ja vou te transferir", "vou te encaminhar",
]


def _promised_transfer_without_acting(text: str) -> bool:
    """Detecta quando a IA escreveu no texto que vai transferir/encaminhar
    pra um humano — usado como rede de segurança pra garantir que a
    ferramenta transferir_atendimento seja realmente chamada quando isso
    acontece (a instrução de prompt pra fazer as duas coisas juntas nem
    sempre é seguida pelo modelo)."""
    normalized = _normalize_text(text)
    return any(phrase in normalized for phrase in _TRANSFER_PROMISE_PHRASES)


def _mentions_any_plan(text_tokens: set[str], plans: list[Plan]) -> bool:
    """Verdadeiro quando o texto (tipicamente a própria resposta da IA) cita
    o nome de algum plano por extenso — sinal confiável de que o assunto é
    mesmo plano, sem depender do cliente ter digitado o nome da unidade."""
    for plan in plans:
        name_tokens = _normalize_tokens(plan.name)
        if name_tokens and name_tokens <= text_tokens:
            return True
    return False


# O OpenAI só aceita nome de função nesse formato. Uma única ferramenta com
# chave fora dele (acento, espaço, vazia) faz a API recusar a requisição
# INTEIRA com 400 — e como todas as ferramentas ativas vão em todo request,
# isso derruba qualquer resposta da IA, inclusive um "oi". Já aconteceu em
# produção: ninguém respondia e o erro ficava só no log.
_OPENAI_FUNCTION_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _is_valid_openai_tool(tool: Tool) -> bool:
    return bool(_OPENAI_FUNCTION_NAME_RE.match(tool.tool_key or ""))


def _tool_to_openai_schema(tool: Tool) -> dict:
    properties: dict = {}
    required: list[str] = []
    for p in tool.parameters or []:
        param_name = (p.get("name") or "").strip()
        if not param_name:
            continue
        properties[param_name] = {
            "type": p.get("type") or "string",
            "description": p.get("description") or "",
        }
        if p.get("required"):
            required.append(param_name)
    description = tool.description or tool.name
    if tool.tool_key == TOOL_KEY_SEND_PLAN_IMAGES:
        # Reforça aqui, na própria descrição vista na hora de decidir chamar
        # a ferramenta — colocar isso só num system prompt solto no meio do
        # contexto não foi suficiente pra IA parar de oferecer plano sem ser
        # perguntado (testado e confirmado o comportamento errado antes
        # desse reforço).
        description = (
            description + " SÓ chame isso se o cliente pediu explicitamente por planos, preços, "
            "valores ou matrícula nesta mensagem — nunca chame só porque confirmou a unidade pra "
            "responder outra pergunta (horário, endereço, estrutura etc)."
        )
    elif tool.tool_key == TOOL_KEY_VERIFY_UNIT_BY_CPF:
        description = (
            description + " Use SEMPRE que precisar da unidade do ALUNO e ela ainda não estiver "
            "no cadastro — peça SOMENTE o CPF se ainda não tiver (nunca peça a unidade junto), "
            "depois chame esta ferramenta. NÃO pergunte a unidade verbalmente se puder descobrir "
            "pelo CPF. NÃO chame quando o cliente só quiser planos/preços/matrícula de lead novo: "
            "nesse caso pergunte de qual unidade ele quer saber os planos. Depois do sucesso, use "
            "dados.unidade nas próximas ferramentas sem perguntar de novo."
        )
    elif tool.tool_key == TOOL_KEY_TRANSFER:
        description = (
            description + " Se existir uma ferramenta específica pra resolver o pedido (ex: "
            "consultar parcela em atraso, gerar link de pagamento), chame ESSA primeiro — não "
            "transfira só porque é sobre pagamento. Chame transferir_atendimento quando: (1) o "
            "cliente pedir explicitamente por atendente humano; (2) for reclamação, cobrança "
            "errada ou problema de pagamento que NENHUMA ferramenta disponível resolve; (3) "
            "CANCELAMENTO de matrícula; (4) pedido de desconto ou condição especial fora do "
            "catálogo; (5) pergunta fora do que você sabe (RAG/catálogo não cobre, assunto sem "
            "relação com a academia); (6) assunto sensível — lesão, saúde, questão jurídica; (7) "
            "o cliente insistir na mesma dúvida ou pedido mais de 2 vezes mesmo depois de você já "
            "ter respondido; (8) o cliente parecer irritado, indignado, com raiva, ou usar "
            "xingamentos/palavrões. NÃO chame pra dúvidas normais (planos, preços, horários, "
            "endereço, estrutura, matrícula via link) — isso a IA resolve sozinha."
        )
    return {
        "type": "function",
        "function": {
            "name": tool.tool_key,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


async def _log_tool_call(
    db: AsyncSession,
    tool: Tool,
    conversation: Conversation,
    arguments: dict,
    success: bool,
    error_message: str | None = None,
) -> None:
    """Um registro por chamada de ferramenta de verdade, pra métricas tipo
    "quantos pediram link de parcela" ou "taxa de sucesso por ferramenta" —
    Chat de teste não gera log, mesmo critério já usado nas outras métricas."""
    if conversation.channel == "test_console":
        return
    db.add(
        ToolCallLog(
            company_id=conversation.company_id,
            conversation_id=conversation.id,
            tool_id=tool.id,
            tool_key=tool.tool_key,
            tool_name=tool.name,
            success=success,
            arguments=arguments,
            error_message=error_message,
        )
    )
    await db.flush()


async def execute_tool(
    db: AsyncSession,
    tool: Tool,
    arguments: dict,
    conversation: Conversation,
) -> dict:
    if not tool.webhook_url:
        await _log_tool_call(db, tool, conversation, arguments, False, "Ferramenta sem webhook configurado.")
        return {"sucesso": False, "mensagem": "Ferramenta sem webhook configurado."}

    contexto = {
        "telefone_cliente": sanitize_phone_digits(conversation.contact_phone),
        "nome_cliente": conversation.contact_name,
        "conversation_id": str(conversation.id),
        # ID da conversa na plataforma de origem (ex: sessionId do GYMBOT) —
        # sem isso, o sistema externo não consegue localizar a conversa no
        # banco dele mesmo recebendo a chamada da ferramenta.
        "external_conversation_id": conversation.external_conversation_id,
    }
    payload = {
        "ferramenta": tool.tool_key,
        "argumentos": arguments,
        "contexto": contexto,
    }
    is_test = conversation.channel == "test_console"
    if is_test:
        # Chat de teste não deve disparar nada de verdade fora do MovFit IA
        # (workflow do n8n, API do WhatsApp, etc) — só o texto já tinha essa
        # proteção; o webhook da própria ferramenta não tinha, e "testar" o
        # envio de imagem estava executando o workflow de produção de
        # verdade. Simula sucesso pra IA seguir o fluxo normalmente.
        data: dict = {"sucesso": True, "mensagem": "Simulado no chat de teste — nada foi enviado de verdade.", "dados": {}}
        logger.info("Ferramenta %s (%s) simulada no chat de teste: argumentos=%r", tool.name, tool.tool_key, arguments)
    else:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(tool.webhook_url, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ferramenta %s (%s) falhou: %s", tool.name, tool.tool_key, exc)
            await _log_tool_call(db, tool, conversation, arguments, False, str(exc)[:500])
            return {"sucesso": False, "mensagem": "Falha ao executar a ferramenta agora."}

        tool.last_executed_at = datetime.now(timezone.utc)
        logger.info("Ferramenta %s (%s) respondeu: %r", tool.name, tool.tool_key, data)

    if data.get("sucesso"):
        if tool.tool_key == TOOL_KEY_TRANSFER:
            conversation.ai_enabled = False
            conversation.status = "with_human"
            # "was_transferred" é permanente pra métrica ("quantos foram
            # transferidos no total"), mesmo que o stage mude depois de novo
            # (ex: cliente volta a falar com a IA e é resolvido). Detecta
            # pedido de cancelamento pelo motivo que a própria IA já manda
            # ao transferir (critério de transferência já cobre isso) — só
            # métrica, a IA continua sem poder cancelar de verdade.
            wants_cancellation = _mentions_cancellation(str(arguments.get("motivo") or ""))
            await _upsert_lead(
                db,
                conversation.company_id,
                conversation.contact_phone,
                {"estagio": "transferido"},
                sticky_flags={"was_transferred": True, "wants_cancellation": wants_cancellation},
            )
        elif tool.tool_key == TOOL_KEY_END:
            conversation.status = "resolved"
            await _upsert_lead(db, conversation.company_id, conversation.contact_phone, {"estagio": "resolvido"})

        # Qualquer ferramenta que consulte um sistema externo e devolva
        # nome/CPF/e-mail/data de nascimento do cliente (ex: consultar
        # parcelas) já atualiza o cadastro com esse dado — vem confirmado
        # pelo sistema, mais confiável que o que o cliente digitou.
        dados_extra = data.get("dados") if isinstance(data.get("dados"), dict) else {}
        lead_result_fields = {k: v for k, v in {**data, **dados_extra}.items() if k in _LEAD_ARG_KEYS and v}
        if lead_result_fields:
            await _upsert_lead(db, conversation.company_id, conversation.contact_phone, lead_result_fields)

        # Unidade do aluno confirmada: (a) tool mandou unidade+cpf juntos
        # nos argumentos (ex: consultar parcela), ou (b) a tool devolveu
        # unidade em dados (ex: verificar_unidade_por_cpf). Nos dois casos
        # grava no Lead + is_student — reforço no código, não só prompt.
        unidade_confirmada = None
        cpf_ref = arguments.get("cpf") or dados_extra.get("cpf") or data.get("cpf")
        if arguments.get("unidade") and arguments.get("cpf"):
            unidade_confirmada = arguments["unidade"]
        elif dados_extra.get("unidade") and (
            cpf_ref or tool.tool_key == TOOL_KEY_VERIFY_UNIT_BY_CPF
        ):
            unidade_confirmada = dados_extra["unidade"]
        if unidade_confirmada:
            unit_fields: dict = {"unidade": unidade_confirmada}
            if cpf_ref:
                unit_fields["cpf"] = cpf_ref
            await _upsert_lead(
                db,
                conversation.company_id,
                conversation.contact_phone,
                unit_fields,
                stage_if_new="aluno",
                sticky_flags={"is_student": True},
            )

    await _log_tool_call(
        db, tool, conversation, arguments, bool(data.get("sucesso")), None if data.get("sucesso") else data.get("mensagem")
    )
    return data


async def _send_single_plan_image(db: AsyncSession, tool: Tool, image: dict, conversation: Conversation) -> bool:
    """Manda o webhook da ferramenta pra UMA imagem só e espera a resposta
    antes de devolver — usado pra garantir que a legenda (texto) só saia
    depois que a imagem já foi confirmada enviada, mantendo a ordem certa
    (imagem, depois descrição) por plano em vez de mandar tudo de uma vez."""
    is_test = conversation.channel == "test_console"
    payload = {
        "ferramenta": tool.tool_key,
        "argumentos": {"Unidade": image["unidade"], "Nome do Plano": image["plano"]},
        "contexto": {
            "telefone_cliente": sanitize_phone_digits(conversation.contact_phone),
            "nome_cliente": conversation.contact_name,
            "conversation_id": str(conversation.id),
            "external_conversation_id": conversation.external_conversation_id,
            "imagens_planos": [image],
        },
    }
    if is_test:
        data: dict = {"sucesso": True}
    else:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(tool.webhook_url, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ferramenta %s (%s) falhou pra %s: %s", tool.name, tool.tool_key, image["plano"], exc)
            await _log_tool_call(db, tool, conversation, payload["argumentos"], False, str(exc)[:500])
            return False
        tool.last_executed_at = datetime.now(timezone.utc)
        if not data.get("sucesso"):
            await _log_tool_call(db, tool, conversation, payload["argumentos"], False, data.get("mensagem"))
            return False

    await _log_tool_call(db, tool, conversation, payload["argumentos"], True)
    await save_message(
        db,
        conversation,
        NormalizedMessageEvent(
            event_type="message_outbound",
            external_message_id=None,
            external_conversation_id=None,
            channel_to=None,
            contact_phone=conversation.contact_phone,
            content_type="image",
            text=f"{image['plano']} ({image['unidade']})",
            timestamp=datetime.now(timezone.utc),
            actor="ai",
            raw_payload={"images": [image]},
        ),
    )
    return True


async def _present_plan_images_with_captions(
    db: AsyncSession,
    conversation: Conversation,
    company_id: UUID,
    tool: Tool,
    images: list[dict],
    force_resend: bool,
    touched_units: set[str] | None = None,
) -> dict:
    """Apresenta cada plano como imagem seguida da própria legenda/descrição,
    um de cada vez, esperando a imagem ser confirmada antes de mandar o
    texto — a única forma de garantir a ordem imagem->descrição por plano,
    já que imagem (webhook da ferramenta) e texto (outbound da integração)
    são dois canais de entrega sem sincronia nenhuma entre si.

    `touched_units` (compartilhado entre chamadas da mesma resposta) evita
    mandar a introdução "Aqui estão os planos..." mais de uma vez quando a
    IA chama essa ferramenta várias vezes na mesma resposta (ex: uma vez por
    plano) — já vimos isso acontecer de verdade em produção."""
    if not images:
        return {"sucesso": True, "mensagem": "Nenhum plano encontrado pra essa unidade.", "dados": {}}

    if force_resend:
        to_send = images
    else:
        already_sent = await _get_sent_plan_image_urls(db, conversation.id)
        to_send = [img for img in images if img["url"] not in already_sent]
    if not to_send:
        return {"sucesso": True, "mensagem": "Imagens já enviadas anteriormente nesta conversa.", "dados": {}}

    is_test = conversation.channel == "test_console"
    unit_name = to_send[0]["unidade"]
    already_announced = touched_units is not None and unit_name in touched_units
    if touched_units is not None:
        touched_units.add(unit_name)
    if not already_announced:
        intro = f"Aqui estão os planos da unidade {unit_name}:"
        await save_message(
            db,
            conversation,
            NormalizedMessageEvent(
                event_type="message_outbound",
                external_message_id=None,
                external_conversation_id=None,
                channel_to=None,
                contact_phone=conversation.contact_phone,
                content_type="text",
                text=intro,
                timestamp=datetime.now(timezone.utc),
                actor="ai",
                raw_payload={"generated": True},
            ),
        )
        if not is_test:
            await send_outbound(db, company_id, conversation, intro)

    sent_count = 0
    for image in to_send:
        if not await _send_single_plan_image(db, tool, image, conversation):
            continue
        sent_count += 1
        caption = image.get("legenda") or f"{image['plano']} — {image['unidade']}"
        await save_message(
            db,
            conversation,
            NormalizedMessageEvent(
                event_type="message_outbound",
                external_message_id=None,
                external_conversation_id=None,
                channel_to=None,
                contact_phone=conversation.contact_phone,
                content_type="text",
                text=caption,
                timestamp=datetime.now(timezone.utc),
                actor="ai",
                raw_payload={"generated": True, "plan_caption": True},
            ),
        )
        if not is_test:
            await send_outbound(db, company_id, conversation, caption)

    if sent_count == 0:
        return {"sucesso": False, "mensagem": "Falha ao enviar as imagens agora."}
    return {
        "sucesso": True,
        "mensagem": (
            f"{sent_count} plano(s) já apresentados ao cliente, cada um com imagem e descrição "
            "completa. Não repita os detalhes desses planos na sua resposta de texto — só feche "
            "com algo curto, tipo perguntar se quer mais alguma informação."
        ),
        "dados": {},
    }


async def _auto_send_plan_images(
    db: AsyncSession,
    conversation: Conversation,
    user_text: str,
    assistant_text: str,
    tools_by_key: dict[str, Tool],
    touched_units: set[str],
    recent_customer_texts: list[str] | None = None,
) -> None:
    """Rede de segurança: garante que a imagem do plano seja mandada quando o
    cliente menciona uma unidade, mesmo que a IA não decida chamar a
    ferramenta por conta própria (comportamento de prompt não é 100%
    consistente). O dedup ("não repetir a mesma imagem") e o reenvio quando
    o cliente pede explicitamente já são tratados dentro de
    _present_plan_images_with_captions, então essa função só decide QUANDO
    tentar chamar, não SE deve repetir.

    `touched_units` traz as unidades que a própria IA já mandou imagem nessa
    mesma resposta (via chamada de ferramenta) — pula essas pra não mandar a
    mesma imagem duas vezes numa resposta só."""
    tool = tools_by_key.get(TOOL_KEY_SEND_PLAN_IMAGES)
    if not tool or not tool.webhook_url:
        return
    if not _wants_plan_info(user_text, recent_customer_texts):
        # Sem isso, só citar o nome de uma unidade por qualquer outro motivo
        # (ex: "a academia de Itaituba abre hoje?") já disparava o envio dos
        # planos — o cliente só queria saber o horário.
        return

    text_tokens = _normalize_tokens(user_text)

    result = await db.execute(
        select(Unit)
        .options(selectinload(Unit.plans))
        .where(Unit.company_id == conversation.company_id, Unit.is_active.is_(True))
    )
    units = result.scalars().unique().all()
    units_with_images = [u for u in units if any(p.is_active and p.image_url for p in u.plans)]

    unit = None
    if text_tokens:
        candidates = [(str(u.id), _normalize_tokens(f"{u.name} {u.city} {u.unit_type or ''}")) for u in units]
        matched_unit_id = _unique_match(candidates, text_tokens, threshold=0.4)
        if matched_unit_id:
            unit = next((u for u in units if str(u.id) == matched_unit_id), None)

    if not unit and len(units_with_images) == 1:
        # Cliente não citou a unidade (ex: "me mostra os planos"), mas só
        # existe uma cadastrada — sem ambiguidade nenhuma pra resolver. Só
        # dispara se a resposta da IA realmente citar um plano por extenso,
        # pra não mandar imagem sem pedir em mensagens sem nada a ver (tipo
        # um simples "oi, tudo bem?").
        candidate_unit = units_with_images[0]
        combined_tokens = text_tokens | _normalize_tokens(assistant_text)
        if _mentions_any_plan(combined_tokens, candidate_unit.plans):
            unit = candidate_unit

    if not unit:
        return
    if not any(p.is_active and p.image_url for p in unit.plans):
        return

    force_resend = _wants_image_explicitly(user_text)
    if force_resend and unit.name in touched_units:
        # Só pula quando é reenvio explícito: aí sim a IA já tendo mandado a
        # imagem dessa unidade nessa resposta é sinal de duplicata na certa
        # (o dedup por URL não se aplica no reenvio forçado). Fora isso,
        # "unidade já tocada" não quer dizer "todos os planos dela já foram
        # mandados" — a IA pode ter chamado a ferramenta só pra um dos
        # planos da unidade, e o dedup por URL abaixo já cuida de não
        # repetir o que já foi enviado.
        return
    resolved_images = await _resolve_plan_images(db, conversation.company_id, {"Unidade": unit.name})
    await _present_plan_images_with_captions(
        db,
        conversation,
        conversation.company_id,
        tool,
        resolved_images,
        force_resend=force_resend,
        touched_units=touched_units,
    )


async def resolve_ai_config(db: AsyncSession, conversation: Conversation) -> AiConfig | None:
    """Personalização da integração da conversa, com fallback pra
    configuração padrão da empresa — mesma regra usada tanto pra gerar
    resposta normal quanto follow-up de inatividade."""
    config = None
    if conversation.integration_id:
        result = await db.execute(
            select(AiConfig).where(
                AiConfig.company_id == conversation.company_id,
                AiConfig.integration_id == conversation.integration_id,
            )
        )
        config = result.scalar_one_or_none()
    if not config:
        result = await db.execute(
            select(AiConfig).where(
                AiConfig.company_id == conversation.company_id,
                AiConfig.integration_id.is_(None),
            )
        )
        config = result.scalar_one_or_none()
    return config


def compose_base_prompt(config: AiConfig) -> str:
    """Nome, tom de voz e uso de emoji são campos estruturados (editáveis sem
    mexer em prompt) — o prompt "de verdade" é montado a partir deles aqui."""
    ai_name = config.ai_name or "assistente virtual"
    tone_text = (config.tone or "cordial, objetiva e útil").strip()
    emoji_instruction = (
        "Pode usar emoji com moderação, de acordo com o contexto."
        if config.use_emoji
        else "Não use emoji nas respostas."
    )
    return f"Você é a {ai_name}, assistente virtual da Mov Fit. Seu tom de voz: {tone_text}. {emoji_instruction}"


async def generate_ai_reply(
    db: AsyncSession,
    conversation: Conversation,
    user_text: str,
) -> tuple[str | None, tuple[Tool, dict] | None, bool]:
    """Retorna (texto_final, chamada_de_encerramento_adiada,
    conteudo_ja_enviado_via_tools). O terceiro item fica True quando imagens
    ou outros outbound já foram gravados/enviados durante o loop de
    ferramentas — reply_to_pending_messages usa isso pra não descartar a
    resposta só porque final_text veio vazio."""
    if not conversation.ai_enabled:
        return None, None, False

    config = await resolve_ai_config(db, conversation)
    if not config or config.operation_mode == "off":
        return None, None, False
    if not config.llm_api_key_encrypted:
        logger.warning("Empresa %s sem API key LLM", conversation.company_id)
        return None, None, False

    api_key = decrypt_secret(config.llm_api_key_encrypted)
    rag_context = await fetch_rag_context(db, conversation.company_id, user_text)

    history_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc())
        .limit(20)
    )
    history = list(reversed(history_result.scalars().all()))
    is_first_contact = not any(m.actor in {"ai", "human_agent"} for m in history)
    recent_customer_texts = [m.text for m in history if m.actor == "customer" and m.text]
    plan_intent_active = _plan_flow_active(user_text, recent_customer_texts)
    student_operational = _student_operational_action(user_text)
    guest_info_question = _is_guest_info_question(user_text)

    lead_result = await db.execute(
        select(Lead).where(
            Lead.company_id == conversation.company_id,
            Lead.phone == sanitize_phone_digits(conversation.contact_phone),
        )
    )
    lead = lead_result.scalar_one_or_none()
    confirmed_student = bool(lead and (lead.is_student or lead.unit))

    # Resolve ferramentas cedo — o prompt de unidade muda se
    # verificar_unidade_por_cpf estiver ativa nesta integração.
    tools_result = await db.execute(
        select(Tool).where(
            Tool.company_id == conversation.company_id,
            Tool.is_active.is_(True),
            or_(Tool.integration_id.is_(None), Tool.integration_id == conversation.integration_id),
        )
    )
    active_tools = tools_result.scalars().all()
    # Se uma ferramenta global e uma escopada pra essa integração dividem o
    # mesmo tool_key, a escopada vence — é o webhook certo pra essa conversa.
    tools_by_key: dict[str, Tool] = {}
    for t in sorted(active_tools, key=lambda t: t.integration_id is not None):
        tools_by_key[t.tool_key] = t
    active_tools = list(tools_by_key.values())
    has_verify_unit_tool = TOOL_KEY_VERIFY_UNIT_BY_CPF in tools_by_key

    ai_name = config.ai_name or "assistente virtual"
    # system_prompt vira só um espaço pra instrução extra opcional, além do
    # que os campos estruturados (nome/tom/emoji) já cobrem.
    base_prompt = compose_base_prompt(config)
    extra_instructions = (config.system_prompt or "").replace("{ai_name}", ai_name).strip()
    system_prompt = f"{base_prompt}\n\n{extra_instructions}" if extra_instructions else base_prompt
    messages = [{"role": "system", "content": system_prompt}]
    brazil_now = datetime.now(timezone(timedelta(hours=-3)))
    weekday_pt = [
        "segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
        "sexta-feira", "sábado", "domingo",
    ][brazil_now.weekday()]
    messages.append(
        {
            "role": "system",
            "content": (
                f"Agora são {brazil_now.strftime('%H:%M')} de {weekday_pt}, "
                f"{brazil_now.strftime('%d/%m/%Y')} (horário de Brasília) — use isso pra "
                "responder com precisão qualquer pergunta sobre \"hoje\", \"agora\", se a "
                "academia está aberta neste momento, ou horário de funcionamento (que pode "
                "variar por dia da semana e por feriado, conforme os dados da base de "
                "conhecimento ou do catálogo). Se hoje for feriado nacional ou municipal "
                "conhecido, considere isso ao responder sobre horário — mas só se tiver certeza "
                "da data comemorada; não invente feriado."
            ),
        }
    )
    messages.append(
        {
            "role": "system",
            "content": (
                "Formatação: isso vai pro WhatsApp, não markdown de verdade. Use *um "
                "asterisco* pra negrito (nunca **dois**) e _sublinhado_ pra itálico — sem "
                "cabeçalho tipo ### ou ##, sem \"**\" em lugar nenhum. NUNCA use \"---\", "
                "\"***\" ou qualquer linha divisória pra separar seções/planos — isso vira uma "
                "mensagem separada e sem sentido pro cliente; uma linha em branco entre "
                "parágrafos já separa o suficiente. Listas: hífen ou • "
                "simples, sem numerar a menos que a ordem importe. Links: manda a URL pura "
                "(https://...) solta no texto — NUNCA no formato [texto](url) do markdown, "
                "porque o WhatsApp não interpreta isso, aparece literalmente com colchetes e "
                "parênteses pro cliente; uma URL pura o WhatsApp já deixa clicável sozinho. Ao "
                "apresentar planos, não repita a descrição/slogan geral da academia em cada "
                "plano — isso já foi dito (ou nem precisa ser dito) uma vez só. Cada plano "
                "individual segue este modelo visual (adapte os dados de cada plano, mas "
                "mantenha a estrutura, os emojis e as quebras de linha — pule qualquer linha "
                "cujo dado não exista para aquele plano, tipo taxa de matrícula zerada):\n\n"
                "🏋️ *NOME DO PLANO EM MAIÚSCULAS*\n\n"
                "💰 *R$ 000,00 por mês*\n"
                "💳 Pagamento em [forma de pagamento]\n"
                "📅 Fidelidade de N meses\n\n"
                "✅ *Você terá:*\n"
                "• benefício 1\n"
                "• benefício 2\n\n"
                "👉 *Faça sua matrícula pelo link:*\n"
                "https://..."
            ),
        }
    )
    messages.append(
        {
            "role": "system",
            "content": (
                "Tom de conversa — escreva como uma pessoa de verdade atendendo pelo WhatsApp, "
                "não como um bot decorado:\n"
                "1) NÃO termine toda resposta com \"Posso ajudar com mais alguma coisa?\", "
                "\"Estou à disposição!\" ou frases parecidas. Isso é repetitivo e soa robótico "
                "quando aparece em toda mensagem. Só use algo assim quando a conversa realmente "
                "estiver terminando (cliente agradeceu, confirmou que não precisa de mais nada, "
                "ou claramente encerrou o assunto) — no meio de uma troca ativa, só responda a "
                "pergunta e pare.\n"
                "2) NÃO quebre uma resposta simples em várias mensagens separadas só por estilo "
                "(ex: uma frase, depois uma lista em bolha separada, depois uma pergunta em outra "
                "bolha). Escreva como uma pessoa escreveria — um parágrafo natural corrido. "
                "Quebras em bolhas diferentes são só pra listas genuinamente longas (tipo o "
                "catálogo de unidades) ou pra apresentação de planos.\n"
                "3) NUNCA mencione termos técnicos internos pro cliente — coisas como \"houve um "
                "problema ao enviar\", \"falha no sistema\", \"erro ao processar\", \"tentando "
                "novamente\". Se algo não funcionar, siga a conversa naturalmente sem expor isso "
                "— o cliente não precisa saber que existe um sistema por trás.\n"
                "4) NUNCA invente valor, condição ou informação que não esteja no catálogo ou na "
                "base de conhecimento (ex: diária, day use, aula experimental, desconto não "
                "listado, promoção). Se não tiver certeza ou o dado não existir aqui, diga "
                "claramente que não tem essa informação agora e ofereça transferir pra um "
                "atendente confirmar — nunca chute um número. Antes de dizer \"não tenho essa "
                "informação\", olhe o histórico da conversa: se você mesma já respondeu isso há "
                "pouco, não se contradiga — reafirme o que já foi dito em vez de negar.\n"
                "5) Se você disser ao cliente que vai transferir, encaminhar pro atendimento ou "
                "chamar um atendente, você TEM que chamar a ferramenta transferir_atendimento "
                "nessa mesma resposta (se ela estiver disponível) — nunca prometa isso em texto "
                "sem realmente executar a ferramenta. Prometer e não fazer é pior do que não "
                "prometer nada."
            ),
        }
    )
    messages.append(
        {
            "role": "system",
            "content": (
                "IMPORTANTE: se existir uma ferramenta específica disponível pra resolver o que o "
                "cliente está pedindo (ex: consultar parcela/mensalidade em atraso, gerar link de "
                "pagamento), chame ESSA ferramenta primeiro — não transfira direto só porque o "
                "assunto é sobre pagamento. Só transfira se a ferramenta específica não existir, "
                "não resolver, ou o próprio resultado dela indicar que precisa de atendente.\n"
                "Quando transferir pra atendente humano (ferramenta transferir_atendimento), se "
                "ela estiver disponível: (1) cliente pediu explicitamente por atendente; (2) "
                "reclamação, cobrança errada, ou problema de pagamento que NENHUMA ferramenta "
                "disponível resolve (ex: trocar cartão, cobrança duplicada) — se tiver ferramenta "
                "específica pra consultar isso, use-a antes; (3) CANCELAMENTO de matrícula; (4) "
                "pedido de desconto/condição especial fora do catálogo; (5) pergunta fora do que "
                "você sabe (RAG/catálogo não cobre, assunto sem relação com a academia); (6) "
                "assunto sensível — lesão, saúde, questão jurídica; (7) cliente insiste na mesma "
                "dúvida ou pedido mais de 2 vezes mesmo depois de você já ter respondido; (8) "
                "cliente parece irritado, indignado, com raiva, ou usa xingamentos/palavrões. Pra "
                "dúvidas normais (planos, preços, horários, endereço, estrutura, matrícula via "
                "link), resolva sozinha — não transfira à toa."
            ),
        }
    )
    if is_first_contact:
        messages.append(
            {
                "role": "system",
                "content": (
                    "Esta é a primeira mensagem do cliente nesta conversa — ele ainda não "
                    "recebeu nenhuma resposta sua. Antes de tratar da pergunta dele, comece "
                    f"se apresentando de forma calorosa como {ai_name}, atendente virtual da "
                    "Mov Fit, em uma frase curta, e só então continue normalmente. Não repita "
                    "essa apresentação em mensagens seguintes da mesma conversa."
                ),
            }
        )
    if rag_context:
        messages.append(
            {
                "role": "system",
                "content": f"Contexto da base de conhecimento:\n{rag_context}",
            }
        )
    units_result = await db.execute(
        select(Unit.name, Unit.city).where(Unit.company_id == conversation.company_id, Unit.is_active.is_(True))
    )
    active_units_rows = units_result.all()
    active_unit_names = [n for n, _ in active_units_rows]
    if active_unit_names:
        messages.append(
            {
                "role": "system",
                "content": (
                    "Sempre que uma ferramenta pedir o parâmetro \"unidade\", preencha com o "
                    "nome EXATO da unidade como está cadastrado (veja a lista abaixo/adiante) — "
                    "nunca parafraseie, abrevie ou use o tipo/apelido dela. Ex: se a unidade se "
                    "chama \"" + active_unit_names[0] + "\", use exatamente esse texto, não uma "
                    "descrição tipo cidade+tipo. Isso é necessário pra sistemas externos "
                    "reconhecerem a unidade corretamente."
                ),
            }
        )
    if len(active_unit_names) > 1:
        cities: dict[str, list[str]] = {}
        for name, city in active_units_rows:
            cities.setdefault(city, []).append(name)
        ambiguous_note = ""
        ambiguous_cities = {city: names for city, names in cities.items() if len(names) > 1}
        if ambiguous_cities:
            pairs = "; ".join(f"{city}: {' ou '.join(names)}" for city, names in ambiguous_cities.items())
            ambiguous_note = (
                " ATENÇÃO: mais de uma unidade divide a mesma cidade (" + pairs + "). Se o "
                "cliente disser só o nome da cidade (ex: apenas \"Santarém\"), isso NÃO "
                "identifica qual das duas ele quer — pergunte de novo especificando as opções "
                "pelo nome completo de cada uma, não escolha nenhuma e não mostre informação de "
                "nenhuma das duas ainda."
            )
        messages.append(
            {
                "role": "system",
                "content": (
                    "Esta academia tem HOJE, atualmente, estas unidades (lista sempre atual — "
                    "ignore qualquer outro nome de unidade citado antes nesta conversa, mesmo por "
                    "você mesma, se ele não estiver nesta lista): " + ", ".join(active_unit_names) + "." +
                    ambiguous_note + " Informações como horário de funcionamento, endereço, estrutura, "
                    "estacionamento, aulas e planos variam de unidade para unidade. NUNCA responda "
                    "com informação de mais de uma unidade na mesma mensagem — isso satura o "
                    "cliente. Se o cliente já informou a unidade antes na conversa sem ambiguidade, "
                    "ou ela já está no cadastro dele como aluno, não pergunte de novo — use a que "
                    "já sabemos."
                    + (
                        " REGRA DE DESCOBERTA DE UNIDADE: (1) Pedido de PLANOS/preços/matrícula "
                        "(lead novo) — se ainda não souber de qual unidade ele quer saber os planos, "
                        "PERGUNTE a unidade; NÃO use verificar_unidade_por_cpf pra isso. (2) Ação "
                        "de ALUNO (parcela, convidados, acesso, ou qualquer ferramenta que precise "
                        "da unidade onde ele é matriculado) — NÃO pergunte a unidade; peça SOMENTE "
                        "o CPF (se ainda não tiver), confirme se é aluno se necessário, depois chame "
                        "verificar_unidade_por_cpf antes das outras ferramentas. (3) Horário/endereço/"
                        "estrutura sem ser aluno confirmado — pergunte a unidade normalmente."
                        if has_verify_unit_tool
                        else (
                            " Se o cliente perguntar algo assim e ainda não tiver dito nesta "
                            "conversa, de forma inequívoca, qual unidade específica é a dele, "
                            "PERGUNTE primeiro qual unidade antes de responder — como um "
                            "atendente humano faria."
                        )
                    )
                ),
            }
        )
    if has_verify_unit_tool:
        messages.append(
            {
                "role": "system",
                "content": (
                    "Ferramenta verificar_unidade_por_cpf: descubra a unidade do aluno pelo CPF "
                    "sempre que uma ação/ferramenta precisar dessa unidade e ela ainda não estiver "
                    "no cadastro. Fluxo: se não souber se é aluno, pergunte; se for aluno e não "
                    "tiver CPF, peça SOMENTE o CPF — NUNCA peça a unidade junto, a ferramenta "
                    "descobre sozinha; com o CPF, chame verificar_unidade_por_cpf e use "
                    "dados.unidade daí em diante. Exceção: quem só pergunta planos/preços — aí "
                    "pergunte a unidade do interesse dele, sem buscar por CPF."
                ),
            }
        )
    if guest_info_question and not confirmed_student:
        messages.append(
            {
                "role": "system",
                "content": (
                    "O cliente perguntou sobre convidados de forma geral (ainda não confirmado "
                    "como aluno). Responda com a política/regras da base de conhecimento (RAG) — "
                    "como funciona levar convidados, limites gerais se estiverem lá, etc. "
                    "Também pergunte se ele já é aluno matriculado; se disser que sim e quiser "
                    "saber quantos convites ainda tem no mês, aí peça SOMENTE o CPF e use as "
                    "ferramentas de aluno. NUNCA fique sem responder — sempre mande texto pro "
                    "cliente neste turno."
                ),
            }
        )
    elif has_verify_unit_tool and student_operational and not confirmed_student:
        messages.append(
            {
                "role": "system",
                "content": (
                    "NESTE TURNO o cliente pediu uma AÇÃO de aluno no sistema (ex: parcelas "
                    "atrasadas, consultar convites do mês). Com verificar_unidade_por_cpf "
                    "disponível, NÃO peça em qual unidade ele está matriculado — peça SOMENTE "
                    "o CPF se ainda não tiver, chame a ferramenta, e só então use a ferramenta "
                    "específica do pedido. Sempre responda com texto — nunca deixe o cliente "
                    "sem resposta."
                ),
            }
        )
    catalog_context = await build_catalog_context(db, conversation.company_id)
    if catalog_context:
        messages.append(
            {
                "role": "system",
                "content": (
                    "[Planos]\n"
                    "REGRA: só fale de planos, preços, ou chame enviar_imagens_planos quando o "
                    "cliente pedir isso explicitamente (palavras como plano, preço, valor, "
                    "mensalidade, matrícula, assinar, contratar). Se o cliente confirmou a "
                    "unidade pra responder OUTRA pergunta (horário, endereço, estrutura, "
                    "estacionamento etc), responda SÓ essa pergunta — não aproveite pra oferecer "
                    "planos por conta própria, mesmo que pareça prestativo; isso confunde o "
                    "cliente que só queria uma informação simples.\n"
                    "Pra planos: se ele ainda não disse de qual unidade quer saber, PERGUNTE a "
                    "unidade do interesse — não use CPF/verificar_unidade_por_cpf pra descobrir "
                    "(quem pergunta plano costuma ser lead novo, não aluno matriculado).\n\n"
                    "Catálogo oficial de unidades e planos, sempre atualizado — use isso, "
                    "não invente valores fora daqui. Isso vale MAIS que qualquer coisa dita "
                    "antes nesta conversa (inclusive por você mesma): se uma unidade, plano ou "
                    "preço mencionado anteriormente não aparecer aqui embaixo, ele não existe "
                    "mais — não repita. Quando o cliente confirmar qual plano "
                    "específico ele quer (não só a unidade), e esse plano tiver um \"Link de "
                    "cadastro\", envie esse link exatamente como está aqui — não invente nem "
                    "reproduza de memória. A matrícula é sempre feita pelo próprio cliente "
                    "nesse link (autoatendimento) — você não coleta dados nem processa a "
                    "matrícula pelo chat. Então, quando o cliente disser que quer se matricular, "
                    "assinar ou fechar algum desses planos, a resposta é: confirme qual plano, e "
                    "diga claramente que é só acessar aquele link e completar o cadastro por lá "
                    "(não diga que vai encaminhar pra um atendente nesse caso — isso é só quando "
                    "o plano não tem link nenhum aqui embaixo). Por isso, ao terminar de "
                    "apresentar plano(s), não ofereça ajuda pra \"finalizar a matrícula\" nem "
                    "diga \"se quiser se matricular, me avise\" — isso sugere que você vai fazer "
                    "algo a mais, e não vai (o link já resolve sozinho). Prefira fechar com algo "
                    "neutro tipo \"Deseja mais alguma informação?\" ou \"Posso ajudar com mais "
                    "alguma coisa?\".\n\n"
                    "REGRA CRÍTICA sobre a ferramenta enviar_imagens_planos: toda vez que você "
                    "chamar essa ferramenta, a imagem E a descrição completa (nome, valor, "
                    "fidelidade, benefícios, link) de cada plano já são enviadas automaticamente "
                    "pro cliente, uma de cada vez, nesse exato momento da chamada — antes até de "
                    "você escrever sua resposta de texto. Por isso, depois de chamar essa "
                    "ferramenta, sua resposta de texto final NÃO deve repetir nome, preço, "
                    "fidelidade, benefícios ou link de nenhum desses planos — o cliente já "
                    "recebeu tudo isso. Só complemente com algo bem curto (ex: \"Posso ajudar "
                    "com mais alguma coisa?\"). Escrever a descrição do plano de novo no texto "
                    "duplica a informação pro cliente, que é o erro mais comum aqui — evite.\n" + catalog_context
                ),
            }
        )
    known_fields = []
    if lead:
        if lead.name:
            known_fields.append(f"nome: {lead.name}")
        if lead.cpf:
            known_fields.append(f"CPF: {lead.cpf}")
        if lead.email:
            known_fields.append(f"e-mail: {lead.email}")
        if lead.birthdate:
            known_fields.append(f"data de nascimento: {lead.birthdate.isoformat()}")
        if lead.unit:
            known_fields.append(f"unidade onde já é aluno confirmado: {lead.unit}")
        known_fields.append(f"estágio atual: {lead.stage}")
    if known_fields:
        messages.append(
            {
                "role": "system",
                "content": (
                    "Dados já cadastrados deste cliente (de conversas/mensagens anteriores, "
                    "não precisa perguntar de novo): " + "; ".join(known_fields) + ". Se o "
                    "cliente informar um valor diferente pra algum desses campos agora, chame "
                    "salvar_dado_cliente pra atualizar."
                ),
            }
        )
    if lead and lead.unit:
        messages.append(
            {
                "role": "system",
                "content": (
                    f"Esse cliente já é aluno confirmado da unidade \"{lead.unit}\" (dado "
                    "confirmado por sistema externo, não pelo que ele digitou). Se ele "
                    "perguntar sobre horários, planos ou qualquer outro assunto sem "
                    "especificar outra unidade claramente, assuma que é sobre essa — não use "
                    "a última unidade que ele citou de passagem em outro contexto (ex: "
                    "perguntando o horário de uma unidade diferente só por curiosidade)."
                ),
            }
        )
    elif (
        has_verify_unit_tool
        and lead
        and lead.cpf
        and not lead.unit
        and not plan_intent_active
    ):
        messages.append(
            {
                "role": "system",
                "content": (
                    "Esse cliente já tem CPF no cadastro, mas ainda sem unidade de aluno. "
                    "Antes de chamar qualquer ferramenta de ALUNO que precise da unidade "
                    "dele, chame "
                    f"{TOOL_KEY_VERIFY_UNIT_BY_CPF} com esse CPF — não pergunte a unidade. "
                    "Exceção: se o assunto atual for planos/preços/matrícula (lead novo), "
                    "ignore isso e use a unidade que o cliente informou de interesse."
                ),
            }
        )
    messages.append(
        {
            "role": "system",
            "content": (
                "Se o resultado de alguma ferramenta trouxer o nome real do cliente (campo "
                "\"nome\" na resposta), passe a chamá-lo por esse nome pelo resto da conversa — "
                "é mais confiável que o que ele mesmo escreveu, porque vem confirmado pelo "
                "sistema. Use só o primeiro nome, de forma natural, sem soar formal demais."
            ),
        }
    )

    for m in history:
        role = "assistant" if m.actor in {"ai", "human_agent"} else "user"
        if m.text:
            messages.append({"role": role, "content": m.text})

    # TOOL_KEY_CHECK_SESSION é só pro follow-up consultar por conta própria
    # (ver TOOL_KEY_CHECK_SESSION acima) — nunca deve ser oferecida como uma
    # função que a IA decide chamar durante a conversa.
    offered_tools = [t for t in active_tools if t.tool_key != TOOL_KEY_CHECK_SESSION]
    for t in offered_tools:
        if not _is_valid_openai_tool(t):
            # Deixa essa ferramenta de fora em vez de derrubar a resposta
            # toda (ver _OPENAI_FUNCTION_NAME_RE): melhor a IA atender sem
            # uma ferramenta do que o cliente ficar sem nenhuma resposta.
            logger.error(
                "Ferramenta %r tem tool_key inválida pro OpenAI (%r) e foi ignorada — "
                "use só letras, números, _ ou - na chave.",
                t.name,
                t.tool_key,
            )
    tool_defs = [
        _tool_to_openai_schema(t) for t in offered_tools if _is_valid_openai_tool(t)
    ] + [SAVE_LEAD_DATA_TOOL_SCHEMA]

    assistant_message = await chat_completion(
        provider=config.llm_provider,
        model=config.llm_model,
        api_key=api_key,
        messages=messages,
        temperature=config.temperature,
        tools=tool_defs,
    )

    touched_units: set[str] = set()
    # encerrar_atendimento fecha a sessão de verdade no sistema externo (ex:
    # WTS/GYMBOT) — se isso rodar ANTES da mensagem final ser mandada, o
    # próprio envio da mensagem de despedida reabre uma sessão nova (visto
    # acontecer em produção). Por isso a execução real é adiada pra depois
    # do texto final ser enviado ao cliente — ver reply_to_pending_messages.
    deferred_end_call: tuple[Tool, dict] | None = None
    max_tool_rounds = 4
    rounds = 0
    while assistant_message.get("tool_calls") and rounds < max_tool_rounds:
        rounds += 1
        messages.append(
            {
                "role": "assistant",
                "content": assistant_message.get("content"),
                "tool_calls": assistant_message["tool_calls"],
            }
        )
        for call in sorted(
            assistant_message["tool_calls"],
            # Descobrir unidade pelo CPF antes das outras tools da mesma
            # rodada — senão parcela/convidados rodam sem unidade e o
            # autofill ainda não tem o Lead atualizado.
            key=lambda c: 0 if (c.get("function") or {}).get("name") == TOOL_KEY_VERIFY_UNIT_BY_CPF else 1,
        ):
            fn = call.get("function") or {}
            key = fn.get("name")
            try:
                arguments = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}

            if lead and key != TOOL_KEY_SAVE_LEAD_DATA:
                # Preenche automaticamente com dado já cadastrado do cliente
                # (nome, CPF, e-mail, data de nascimento) quando a ferramenta
                # pede um desses campos e a IA não preencheu — evita
                # perguntar de novo algo que já sabemos, e garante o valor
                # certo mesmo se a IA "esquecer" de usar o que já está
                # salvo (garantia no código, não só instrução no prompt).
                autofill_tool = tools_by_key.get(key)
                if autofill_tool:
                    declared_params = {p.get("name") for p in (autofill_tool.parameters or [])}
                    known_values = {
                        "cpf": lead.cpf,
                        "nome": lead.name,
                        "email": lead.email,
                        "data_nascimento": lead.birthdate.isoformat() if lead.birthdate else None,
                    }
                    # Unidade confirmada (ex: via verificar_unidade_por_cpf) —
                    # preenche tools de aluno. NÃO preenche enviar_imagens_planos:
                    # pedido de plano pergunta a unidade de interesse (lead novo),
                    # que pode ser outra da unidade onde ele já treina.
                    if lead.unit and key != TOOL_KEY_SEND_PLAN_IMAGES:
                        known_values["unidade"] = lead.unit
                        known_values["Unidade"] = lead.unit
                    for arg_name, known_value in known_values.items():
                        if known_value and arg_name in declared_params and not arguments.get(arg_name):
                            arguments[arg_name] = known_value

            if key != TOOL_KEY_SAVE_LEAD_DATA:
                # Rede de segurança: qualquer ferramenta que receba CPF, nome,
                # e-mail ou data de nascimento como argumento já salva isso no
                # cadastro do cliente também — não dá pra confiar que a IA vá
                # lembrar de chamar salvar_dado_cliente à parte toda vez que
                # esses dados aparecerem a serviço de outra ferramenta (já
                # visto não acontecer na prática).
                lead_fields = {k: v for k, v in arguments.items() if k in _LEAD_ARG_KEYS and v}
                if lead_fields:
                    lead = await _upsert_lead(
                        db, conversation.company_id, conversation.contact_phone, lead_fields
                    ) or lead

            if key == TOOL_KEY_SAVE_LEAD_DATA:
                # Ferramenta interna — não passa por webhook nenhum, grava
                # direto no cadastro do cliente.
                lead = await _upsert_lead(
                    db, conversation.company_id, conversation.contact_phone, arguments
                ) or lead
                result = {"sucesso": True, "mensagem": "Dado salvo.", "dados": {}}
                messages.append(
                    {"role": "tool", "tool_call_id": call.get("id"), "content": json.dumps(result, ensure_ascii=False)}
                )
                continue

            if key == TOOL_KEY_SEND_PLAN_IMAGES:
                tool = tools_by_key.get(key)
                if not tool:
                    result = {"sucesso": False, "mensagem": f"Ferramenta '{key}' não encontrada."}
                else:
                    resolved_images = await _resolve_plan_images(db, conversation.company_id, arguments)
                    result = await _present_plan_images_with_captions(
                        db,
                        conversation,
                        conversation.company_id,
                        tool,
                        resolved_images,
                        force_resend=_wants_image_explicitly(user_text),
                        touched_units=touched_units,
                    )
                messages.append(
                    {"role": "tool", "tool_call_id": call.get("id"), "content": json.dumps(result, ensure_ascii=False)}
                )
                continue

            tool = tools_by_key.get(key)
            if key == TOOL_KEY_END and tool and tool.webhook_url:
                # Não executa agora — só guarda pra rodar depois que a
                # mensagem final for enviada (ver comentário acima). A IA
                # segue o fluxo normal como se já tivesse funcionado.
                deferred_end_call = (tool, arguments)
                result = {"sucesso": True, "mensagem": "Atendimento será encerrado após a resposta final."}
            elif tool:
                result = await execute_tool(db, tool, arguments, conversation)
                # Lead pode ter ganhado unidade/cpf nesse execute — recarrega
                # pra autofill das próximas tools da mesma rodada.
                if result.get("sucesso"):
                    lead_result = await db.execute(
                        select(Lead).where(
                            Lead.company_id == conversation.company_id,
                            Lead.phone == sanitize_phone_digits(conversation.contact_phone),
                        )
                    )
                    refreshed = lead_result.scalar_one_or_none()
                    if refreshed:
                        lead = refreshed
            else:
                result = {"sucesso": False, "mensagem": f"Ferramenta '{key}' não encontrada."}

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

        assistant_message = await chat_completion(
            provider=config.llm_provider,
            model=config.llm_model,
            api_key=api_key,
            messages=messages,
            temperature=config.temperature,
            tools=tool_defs,
        )

    final_text = assistant_message.get("content") or ""
    await _auto_send_plan_images(
        db,
        conversation,
        user_text,
        final_text,
        tools_by_key,
        touched_units,
        recent_customer_texts=recent_customer_texts,
    )
    delivered_via_tools = bool(touched_units)

    transfer_tool = tools_by_key.get(TOOL_KEY_TRANSFER)
    if (
        transfer_tool
        and transfer_tool.webhook_url
        and conversation.status != "with_human"
        and _promised_transfer_without_acting(final_text)
    ):
        # Rede de segurança: a IA escreveu que ia transferir mas não chamou
        # a ferramenta nessa resposta — chama por ela, senão vira promessa
        # vazia pro cliente (já visto acontecer de verdade em produção).
        await execute_tool(
            db,
            transfer_tool,
            {"motivo": "Assunto fora do que a IA consegue resolver — ela já sinalizou a transferência ao cliente."},
            conversation,
        )

    return final_text, deferred_end_call, delivered_via_tools


async def send_outbound(
    db: AsyncSession,
    company_id: UUID,
    conversation: Conversation,
    text: str,
) -> None:
    if conversation.integration_id:
        # Conversa tem origem conhecida — manda só pra ela, não pra todas as
        # integrações ativas da empresa (senão uma resposta de um canal
        # vazaria/duplicaria pros outros).
        stmt = select(Integration).where(
            Integration.id == conversation.integration_id,
            Integration.is_active.is_(True),
            Integration.outbound_url.is_not(None),
        )
    else:
        # Conversa antiga ou sem integração rastreada (ex: número cadastrado
        # manualmente) — mantém o comportamento anterior como fallback.
        stmt = select(Integration).where(
            Integration.company_id == company_id,
            Integration.is_active.is_(True),
            Integration.outbound_url.is_not(None),
        )
    result = await db.execute(stmt)
    integrations = result.scalars().all()
    payload = {
        "conversation_id": str(conversation.id),
        "external_conversation_id": conversation.external_conversation_id,
        "contact_phone": conversation.contact_phone,
        "text": text,
        "actor": "ai",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        for integ in integrations:
            if integ.adapter_key == "evolution_api_v1":
                await _send_evolution_outbound(db, client, integ, conversation, text)
                continue
            if not integ.outbound_url:
                continue
            try:
                await client.post(integ.outbound_url, json=payload)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Outbound para %s falhou: %s", integ.name, exc)


def _sanitize_evolution_number(phone: str) -> str:
    return sanitize_phone_digits(phone)


async def _send_evolution_outbound(
    db: AsyncSession,
    client: httpx.AsyncClient,
    integration: Integration,
    conversation: Conversation,
    text: str,
) -> None:
    config = integration.config or {}
    instance_name = config.get("instance_name") or config.get("instanceName")
    encrypted_token = config.get("instance_token_encrypted")
    if not integration.outbound_url or not instance_name or not encrypted_token:
        logger.warning("Integração Evolution %s incompleta para outbound", integration.name)
        return

    number = _sanitize_evolution_number(conversation.contact_phone)
    if not number:
        logger.warning("Número inválido para Evolution outbound: %s", conversation.contact_phone)
        return

    url = f"{integration.outbound_url.rstrip('/')}/message/sendText/{instance_name}"
    body = {
        "number": number,
        "textMessage": {"text": text},
    }
    headers = {
        "apikey": decrypt_secret(str(encrypted_token)),
        "Content-Type": "application/json",
    }
    log = WebhookLog(
        company_id=integration.company_id,
        integration_id=integration.id,
        direction="outbound",
        status="received",
        payload={"url": url, "body": body},
    )
    db.add(log)
    await db.flush()
    try:
        response = await client.post(url, headers=headers, json=body)
        log.http_status = response.status_code
        log.status = "ok" if response.status_code < 400 else "error"
        if response.status_code >= 400:
            log.error_message = response.text
            logger.warning("Evolution outbound %s falhou: %s", integration.name, response.text)
    except Exception as exc:  # noqa: BLE001
        log.status = "error"
        log.error_message = str(exc)
        logger.warning("Evolution outbound %s falhou: %s", integration.name, exc)


async def reply_to_pending_messages(
    db: AsyncSession,
    conversation_id: UUID,
    company_id: UUID,
) -> str | None:
    """Responde de uma vez às mensagens do cliente ainda não respondidas.

    Chamada pelo debounce depois que o cliente fica em silêncio: junta todas
    as mensagens inbound consecutivas mais recentes (a "rajada") em um único
    texto e gera uma única resposta da IA para elas.
    """
    conversation = await db.get(Conversation, conversation_id)
    if not conversation or not conversation.ai_enabled:
        return None

    history_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc())
        .limit(20)
    )
    history = list(reversed(history_result.scalars().all()))

    pending_texts: list[str] = []
    for m in reversed(history):
        if m.actor == "customer" and m.direction == "inbound":
            if m.text:
                pending_texts.insert(0, m.text)
        else:
            break
    if not pending_texts:
        return None

    combined_text = "\n".join(pending_texts)
    reply, deferred_end_call, delivered_via_tools = await generate_ai_reply(
        db, conversation, combined_text
    )
    if not reply:
        if deferred_end_call:
            # A IA decidiu encerrar mas não escreveu nenhum texto de
            # despedida (acontece) — o cliente ainda precisa ser avisado
            # antes da sessão fechar de verdade, e o encerramento adiado
            # precisa rodar de qualquer jeito, senão a ferramenta nunca
            # executa (visto em produção: turno silencioso, nada disparava).
            reply = "Fico feliz em ajudar! Se precisar de mais alguma coisa, é só chamar por aqui. Até mais! 😊"
        elif delivered_via_tools:
            # enviar_imagens_planos (ou rede de segurança) já mandou intro,
            # imagens e legendas — final_text vazio é esperado nesse fluxo.
            if deferred_end_call:
                end_tool, end_arguments = deferred_end_call
                await execute_tool(db, end_tool, end_arguments, conversation)
            return ""
        else:
            return None

    settings = get_settings()
    bubbles = split_into_bubbles(reply, settings.ai_bubble_max_chars)
    is_test = conversation.channel == "test_console"

    for i, bubble_text in enumerate(bubbles):
        out_event = NormalizedMessageEvent(
            event_type="message_outbound",
            external_message_id=None,
            external_conversation_id=conversation.external_conversation_id,
            channel_to=None,
            contact_phone=conversation.contact_phone,
            content_type="text",
            text=bubble_text,
            timestamp=datetime.now(timezone.utc),
            actor="ai",
            raw_payload={
                "generated": True,
                "aggregated_messages": len(pending_texts),
                "bubble": f"{i + 1}/{len(bubbles)}",
            },
        )
        await save_message(db, conversation, out_event)
        if not is_test:
            await send_outbound(db, company_id, conversation, bubble_text)
        if i < len(bubbles) - 1:
            await asyncio.sleep(settings.ai_bubble_delay_seconds)

    if deferred_end_call:
        # Só encerra de verdade (fecha a sessão no sistema externo) DEPOIS
        # de mandar a mensagem final — encerrar antes faz o próprio envio
        # reabrir uma sessão nova em plataformas tipo WTS/GYMBOT.
        end_tool, end_arguments = deferred_end_call
        await execute_tool(db, end_tool, end_arguments, conversation)

    return reply


async def process_normalized_event(
    db: AsyncSession,
    company_id: UUID,
    event: NormalizedMessageEvent,
    number: Number | None = None,
    integration_id: UUID | None = None,
) -> dict:
    if event.event_type == "status_update" and not event.text:
        return {"status": "ignored", "reason": "status_without_text"}

    conversation = await get_or_create_conversation(db, company_id, event, number, integration_id=integration_id)
    message = await save_message(db, conversation, event)

    ai_reply_scheduled = False
    if event.event_type == "message_inbound" and event.actor == "customer" and conversation.ai_enabled:
        settings = get_settings()
        schedule_ai_reply(conversation.id, company_id, settings.ai_reply_debounce_seconds)
        ai_reply_scheduled = True

    return {
        "status": "ok",
        "conversation_id": str(conversation.id),
        "message_id": str(message.id),
        "ai_enabled": conversation.ai_enabled,
        "ai_reply_scheduled": ai_reply_scheduled,
    }
