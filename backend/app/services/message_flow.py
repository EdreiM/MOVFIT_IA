from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

import httpx
from sqlalchemy import func, or_, select
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
    if not sources:
        return ""

    # Formato do webhook n8n Mov Fit
    # - pergunta: texto usado na busca semântica
    # - contexto.plano_em_negociacao: opcional; só preencher quando soubermos o plano
    payload = {
        "pergunta": query.strip(),
        "contexto": {
            "plano_em_negociacao": None,
        },
    }

    async def _fetch_one(source: RagSource) -> str | None:
        try:
            started = datetime.now(timezone.utc)
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.post(source.webhook_url, json=payload)
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            source.last_latency_ms = latency
            if resp.status_code < 400:
                source.last_success_at = datetime.now(timezone.utc)
                data = resp.json()
                text = _extract_rag_text(data)
                if text:
                    return f"[{source.name}]\n{text}"
        except Exception as exc:  # noqa: BLE001
            logger.warning("RAG %s falhou: %s", source.name, exc)
        return None

    chunks: list[str] = []
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*[_fetch_one(s) for s in sources], return_exceptions=True),
            timeout=20.0,
        )
    except asyncio.TimeoutError:
        logger.warning("RAG timeout global (20s) para empresa %s — segue sem contexto extra", company_id)
        results = []
    for item in results:
        if isinstance(item, str) and item:
            chunks.append(item)
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
# Consulta horários livres para avaliação física (n8n/Pacto) — só lista, não confirma.
TOOL_KEY_CHECK_SCHEDULE = "consultar_agendamento_horarios"
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
# Resposta de webhook n8n mal configurado pode vazar template literal —
# gravar isso no Lead derruba o flush (cpf VARCHAR(20)) e silencia o turno.
_UNRESOLVED_TEMPLATE_MARKERS = ("={{", "${", "$('", "{{$", "{{ $")


def _looks_like_unresolved_template(value: object) -> bool:
    s = str(value).strip()
    return bool(s) and any(marker in s for marker in _UNRESOLVED_TEMPLATE_MARKERS)


def _sanitize_cpf(value: object) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw or _looks_like_unresolved_template(raw):
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) != 11:
        return None
    return digits


def _sanitize_lead_fields(fields: dict) -> dict:
    cleaned: dict = {}
    for key, value in fields.items():
        if value is None or value == "":
            continue
        if key == "cpf":
            cpf = _sanitize_cpf(value)
            if cpf:
                cleaned["cpf"] = cpf
            elif _looks_like_unresolved_template(value):
                logger.warning("CPF ignorado — template n8n não resolvido: %r", value)
            continue
        if _looks_like_unresolved_template(value):
            logger.warning("Campo %s ignorado — template n8n não resolvido: %r", key, value)
            continue
        cleaned[key] = value
    return cleaned


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
    fields = _sanitize_lead_fields(fields)
    if not fields and stage_if_new is None and not sticky_flags:
        result = await db.execute(select(Lead).where(Lead.company_id == company_id, Lead.phone == phone_digits))
        return result.scalar_one_or_none()
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
    "agendar", "agendamento", "avaliacao", "avaliacoes",
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


def _is_guest_operational_check(text: str, lead: Lead | None = None) -> bool:
    """Aluno consultando saldo/uso no sistema — não curioso sobre a política geral."""
    tokens = _normalize_tokens(text)
    if not tokens & {"convidado", "convidados", "convite", "convites"}:
        return False
    if tokens & {"restante", "restantes", "saldo", "usei", "utilizei", "limite", "mes"}:
        return True
    if tokens & {"meu", "minha", "meus", "minhas"}:
        return True
    if tokens & {"consultar", "verificar", "checar", "confere"}:
        return True
    if lead and (lead.is_student or lead.unit) and tokens & {"quantos", "quantas"}:
        return True
    if ("aluno" in tokens or "matriculado" in tokens) and tokens & {"sou", "ja", "sim"}:
        return True
    return False


def _is_guest_info_question(text: str, lead: Lead | None = None) -> bool:
    """Dúvida informativa sobre convidados — RAG/explicação, não ferramenta ainda."""
    tokens = _normalize_tokens(text)
    if not tokens & {"convidado", "convidados", "convite", "convites"}:
        return False
    return not _is_guest_operational_check(text, lead)


def _confirms_is_student(text: str) -> bool:
    tokens = _normalize_tokens(text)
    if tokens & {"nao", "nunca", "ainda"}:
        return False
    if ("aluno" in tokens or "matriculado" in tokens) and tokens & {"sou", "sim", "ja"}:
        return True
    if tokens in ({"sim"}, {"sou", "aluno"}, {"sim", "sou"}, {"sim", "sou", "aluno"}):
        return True
    return False


def _student_operational_action(text: str, lead: Lead | None = None) -> bool:
    """Ação de aluno que exige CPF/unidade/ferramenta — só a mensagem ATUAL."""
    if not text or _is_guest_info_question(text, lead):
        return False
    tokens = _normalize_tokens(text)
    if tokens & {"parcela", "parcelas", "atrasad", "atraso", "inadimpl", "boleto", "boletos"}:
        return True
    if tokens & {"convidado", "convidados", "convite", "convites"}:
        return _is_guest_operational_check(text, lead)
    if "matricula" in tokens or "matriculado" in tokens or "aluno" in tokens:
        if tokens & {"minha", "meu", "minhas", "meus", "atrasad", "atraso", "cancelar", "pagar"}:
            return True
    return False


def _wants_student_action(text: str, recent_customer_texts: list[str] | None = None) -> bool:
    """Alias mantido pros testes — olha só a mensagem atual."""
    return _student_operational_action(text)


_CPF_IN_TEXT_RE = re.compile(r"\b(\d{3}[.\s]?\d{3}[.\s]?\d{3}[-.\s]?\d{2}|\d{11})\b")

# tool_keys internas ou genéricas — não são consulta operacional de aluno.
_INTERNAL_OR_GENERIC_TOOL_KEYS = frozenset(
    {
        TOOL_KEY_VERIFY_UNIT_BY_CPF,
        TOOL_KEY_TRANSFER,
        TOOL_KEY_END,
        TOOL_KEY_SEND_PLAN_IMAGES,
        TOOL_KEY_CHECK_SESSION,
        TOOL_KEY_SAVE_LEAD_DATA,
        TOOL_KEY_CHECK_SCHEDULE,
    }
)

_PHYSICAL_EVAL_KEYWORDS = {"avaliacao", "avaliacoes", "agendar", "agendamento", "marcar", "marca"}
_PHYSICAL_EVAL_BODY_KEYWORDS = {"fisica", "fisico", "fisicas", "fisicos"}
_DATE_YYYYMMDD_RE = re.compile(r"\b(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\b")
_DATE_DMY_RE = re.compile(
    r"\b(0?[1-9]|[12]\d|3[01])[/.-](0?[1-9]|1[0-2])(?:[/.-]((?:20)?\d{2}))?\b"
)
_WEEKDAY_TO_INDEX = {
    "segunda": 0,
    "terca": 1,
    "quarta": 2,
    "quinta": 3,
    "sexta": 4,
    "sabado": 5,
    "domingo": 6,
}


def _extract_cpf_from_text(text: str) -> str | None:
    for match in _CPF_IN_TEXT_RE.finditer(text or ""):
        return _sanitize_cpf(match.group(1))
    return None


def _is_physical_eval_intent(text: str) -> bool:
    """Cliente quer agendar/consultar horários de avaliação física."""
    tokens = _normalize_tokens(text)
    if not tokens & _PHYSICAL_EVAL_KEYWORDS:
        return False
    if tokens & _PHYSICAL_EVAL_BODY_KEYWORDS:
        return True
    if "avaliacao" in tokens and tokens & {"agendar", "agendamento", "marcar", "marca", "quero", "preciso", "gostaria"}:
        return True
    if tokens & {"agendar", "agendamento", "marcar"} and "avaliacao" in tokens:
        return True
    return False


def _physical_eval_in_recent(recent_customer_texts: list[str] | None) -> bool:
    prior = [t for t in (recent_customer_texts or []) if t and t.strip()]
    return any(_is_physical_eval_intent(t) for t in prior[-8:])


def _physical_eval_followup(
    text: str,
    recent_customer_texts: list[str] | None,
    lead: Lead | None = None,
) -> bool:
    """CPF, data, período ou horário depois que o cliente pediu avaliação física."""
    if _is_physical_eval_intent(text):
        return False
    if not _physical_eval_in_recent(recent_customer_texts):
        return False
    if _extract_cpf_from_text(text):
        return True
    brazil_now = datetime.now(timezone(timedelta(hours=-3)))
    if _extract_schedule_date_from_text(text, brazil_now):
        return True
    if _extract_schedule_period_from_text(text):
        return True
    if _extract_schedule_time_from_text(text):
        return True
    tokens = _normalize_tokens(text)
    if tokens & set(_WEEKDAY_TO_INDEX) and len(tokens) <= 5:
        return True
    return False


def _extract_schedule_date_from_text(text: str, reference: datetime) -> str | None:
    """Converte data natural ou yyyyMMdd/dd/mm para yyyyMMdd."""
    if not text:
        return None
    raw = text.strip()
    digits_only = re.sub(r"\D", "", raw)
    if len(digits_only) == 8 and digits_only.startswith("20"):
        return digits_only

    match = _DATE_YYYYMMDD_RE.search(raw)
    if match:
        return f"{match.group(1)}{match.group(2)}{match.group(3)}"

    match = _DATE_DMY_RE.search(raw)
    if match:
        day, month, year = match.group(1), match.group(2), match.group(3)
        if year:
            if len(year) == 2:
                year = f"20{year}"
        else:
            year = str(reference.year)
        return f"{year}{month.zfill(2)}{day.zfill(2)}"

    tokens = _normalize_tokens(raw)
    ref_date = reference.date()

    if "hoje" in tokens:
        target = ref_date
    elif "amanha" in tokens:
        target = ref_date + timedelta(days=1)
    elif ("depois" in tokens and "amanha" in tokens) or "depoisdeamanha" in tokens:
        target = ref_date + timedelta(days=2)
    else:
        target = None
        for name, weekday in _WEEKDAY_TO_INDEX.items():
            if name not in tokens:
                continue
            days_ahead = (weekday - ref_date.weekday()) % 7
            if days_ahead == 0 or "proxima" in tokens or "proximo" in tokens:
                days_ahead = 7 if days_ahead == 0 else days_ahead
            target = ref_date + timedelta(days=days_ahead)
            break
        if target is None:
            return None

    return target.strftime("%Y%m%d")


def _parse_schedule_date_yyyyMMdd(schedule_date: str) -> date | None:
    if not schedule_date or len(schedule_date) != 8:
        return None
    try:
        return date(int(schedule_date[0:4]), int(schedule_date[4:6]), int(schedule_date[6:8]))
    except ValueError:
        return None


def _format_schedule_date_br(schedule_date: str) -> str:
    parsed = _parse_schedule_date_yyyyMMdd(schedule_date)
    if not parsed:
        return schedule_date
    return parsed.strftime("%d/%m/%Y")


def _schedule_date_is_weekend(schedule_date: str) -> bool:
    parsed = _parse_schedule_date_yyyyMMdd(schedule_date)
    return parsed is not None and parsed.weekday() >= 5


def _lead_first_name(lead: Lead | None) -> str | None:
    if not lead or not lead.name:
        return None
    first = lead.name.strip().split()[0]
    return first or None


_SCHEDULE_MORNING_END_MINUTES = 12 * 60  # antes de 12:00 = manhã


def _normalize_schedule_horario(valor: str | None) -> str | None:
    if valor is None:
        return None
    texto = (
        str(valor)
        .strip()
        .lower()
        .replace("às", "")
        .replace("as", "")
        .strip()
    )
    match = re.match(r"^(\d{1,2})\s*h?$", texto)
    if match:
        hora = int(match.group(1))
        if 0 <= hora <= 23:
            return f"{hora:02d}:00"
    match = re.search(r"(?:^|\D)(\d{1,2})\s*(?:h|:)\s*(\d{1,2})(?:\D|$)", texto)
    if match:
        hora, minuto = int(match.group(1)), int(match.group(2))
        if 0 <= hora <= 23 and 0 <= minuto <= 59:
            return f"{hora:02d}:{minuto:02d}"
    return None


def _schedule_horario_to_minutes(horario: str) -> int | None:
    normalizado = _normalize_schedule_horario(horario)
    if not normalizado:
        return None
    hora, minuto = normalizado.split(":")
    return int(hora) * 60 + int(minuto)


def _extract_schedule_time_from_text(text: str) -> str | None:
    if not text:
        return None
    raw = _normalize_text(text)
    match = re.search(r"(?:^|\D)(\d{1,2})\s*(?:h|:)\s*(\d{1,2})(?:\D|$)", raw)
    if match:
        return _normalize_schedule_horario(f"{match.group(1)}:{match.group(2)}")
    match = re.search(r"(?:^|\D)(\d{1,2})\s*h(?:\D|$)", raw)
    if match:
        return _normalize_schedule_horario(match.group(1))
    return _normalize_schedule_horario(text)


def _extract_schedule_period_from_text(text: str) -> str | None:
    tokens = _normalize_tokens(text)
    if "manha" in tokens:
        return "manha"
    if "tarde" in tokens:
        return "tarde"
    return None


def _period_label(period: str | None) -> str:
    if period == "manha":
        return "manhã"
    if period == "tarde":
        return "tarde"
    return ""


def _physical_eval_schedule_preference(
    user_text: str,
    recent_customer_texts: list[str] | None,
    reference: datetime,
) -> dict:
    """Monta dia + período + horário preferido, herdando contexto de mensagens anteriores."""
    prior = [
        t
        for t in (recent_customer_texts or [])
        if t and t.strip() and t.strip() != (user_text or "").strip()
    ]
    scan_order = [user_text] + list(reversed(prior[-8:]))

    schedule_date = _extract_schedule_date_from_text(user_text, reference)
    if not schedule_date:
        for text in scan_order[1:]:
            schedule_date = _extract_schedule_date_from_text(text, reference)
            if schedule_date:
                break

    period = None
    preferred_time = None
    for text in scan_order:
        if not period:
            period = _extract_schedule_period_from_text(text)
        if not preferred_time:
            preferred_time = _extract_schedule_time_from_text(text)

    if preferred_time and not period:
        minutes = _schedule_horario_to_minutes(preferred_time)
        if minutes is not None:
            period = "manha" if minutes < _SCHEDULE_MORNING_END_MINUTES else "tarde"

    return {
        "date": schedule_date,
        "period": period,
        "preferred_time": preferred_time,
    }


def _filter_horarios_by_preference(
    horarios: list[str],
    *,
    period: str | None,
    preferred_time: str | None,
) -> tuple[list[str], dict]:
    """Filtra horários livres por manhã/tarde e destaca se o horário pedido não existe."""
    meta: dict = {}
    if not horarios:
        return [], meta

    normalized_map = {_normalize_schedule_horario(h) or h: h for h in horarios}
    ordered = sorted(
        horarios,
        key=lambda h: _schedule_horario_to_minutes(h) or 0,
    )

    filtered = ordered
    if period == "manha":
        filtered = [
            h
            for h in ordered
            if (_schedule_horario_to_minutes(h) or 0) < _SCHEDULE_MORNING_END_MINUTES
        ]
    elif period == "tarde":
        filtered = [
            h
            for h in ordered
            if (_schedule_horario_to_minutes(h) or 0) >= _SCHEDULE_MORNING_END_MINUTES
        ]

    if period and not filtered:
        meta["periodo_sem_vagas"] = period
        return [], meta

    if preferred_time:
        pref_norm = _normalize_schedule_horario(preferred_time)
        meta["horario_preferido"] = pref_norm or preferred_time
        available_norms = set(normalized_map)
        if pref_norm and pref_norm not in available_norms:
            meta["horario_preferido_indisponivel"] = pref_norm

    return filtered, meta


def _enrich_schedule_result_with_preference(
    tool_result: dict,
    *,
    period: str | None,
    preferred_time: str | None,
) -> dict:
    dados = dict(tool_result.get("dados") or {})
    horarios = dados.get("horarios_disponiveis") or []
    if not isinstance(horarios, list):
        horarios = []

    filtered, meta = _filter_horarios_by_preference(
        [str(h) for h in horarios if h],
        period=period,
        preferred_time=preferred_time,
    )
    dados["horarios_disponiveis"] = filtered
    if period:
        dados["periodo_solicitado"] = period
        dados["periodo_solicitado_label"] = _period_label(period)
    if preferred_time:
        dados["horario_preferido"] = _normalize_schedule_horario(preferred_time) or preferred_time
    dados.update(meta)
    return {**tool_result, "dados": dados}


def _physical_eval_ask_day_reply(unit: str, lead: Lead | None) -> str:
    first = _lead_first_name(lead)
    prefix = f"{first}, encontrei" if first else "Encontrei"
    return (
        f"{prefix} sua matrícula na unidade *{unit}*! 😊 "
        "Para consultar horários de *avaliação física*, me diz um *dia útil* "
        "(segunda a sexta) e se prefere *manhã* ou *tarde* — "
        "pode mandar tipo *segunda de manhã*, *terça às 9h* ou *quarta à tarde*."
    )


def _physical_eval_no_slots_reply(
    schedule_date: str,
    period: str | None,
    lead: Lead | None,
) -> str:
    first = _lead_first_name(lead)
    data_fmt = _format_schedule_date_br(schedule_date)
    greeting = f"{first}, " if first else ""
    periodo_txt = f" no período da *{_period_label(period)}*" if period else ""
    return (
        f"{greeting}consultei o dia *{data_fmt}*{periodo_txt} e não encontrei horários livres "
        "para avaliação física. Quer tentar *outro dia* ou *outro período* (manhã/tarde)?"
    )


def _physical_eval_weekend_reply(schedule_date: str, lead: Lead | None) -> str:
    first = _lead_first_name(lead)
    data_fmt = _format_schedule_date_br(schedule_date)
    greeting = f"{first}, " if first else ""
    return (
        f"{greeting}a avaliação física acontece só de *segunda a sexta*, tá? 😊 "
        f"No dia *{data_fmt}* (fim de semana) não tem agendamento. "
        "Me manda um dia útil que funcione pra você — por exemplo *segunda* ou uma data "
        "de segunda a sexta."
    )


_CUSTOMER_TRANSFER_ON_TOOL_FAILURE = (
    "No momento não tenho acesso a essa informação por aqui. "
    "Vou te encaminhar para um atendente que pode te ajudar melhor com isso. 😊"
)

_INTERNAL_TOOL_FAILURE_PHRASES = (
    "falha ao executar",
    "ferramenta sem webhook",
    "erro interno",
    "ferramenta '",
    "timeout",
)


def _is_technical_tool_failure(result: dict) -> bool:
    """Falha de infra/n8n — cliente não deve ver detalhe; transferir."""
    if result.get("sucesso"):
        return False
    msg = _normalize_text(str(result.get("mensagem") or ""))
    if not msg:
        return True
    return any(phrase in msg for phrase in _INTERNAL_TOOL_FAILURE_PHRASES)


def _tool_result_for_llm(result: dict, tool: Tool | None = None) -> dict:
    """Prepara o retorno da ferramenta pro loop da IA — fatos, não relatório de sistema."""
    if not result.get("sucesso") and _is_technical_tool_failure(result):
        return {
            "sucesso": False,
            "mensagem": (
                "A consulta no sistema não pôde ser concluída. Transfira o cliente para um "
                "atendente humano agora (transferir_atendimento) e diga que no momento você "
                "não tem acesso a essa informação, mas já encaminhou."
            ),
            "dados": {},
        }
    if tool and result.get("sucesso"):
        facts = _tool_facts_for_llm(result, tool)
        facts["instrucao"] = (
            "Informe o cliente em segunda pessoa (você), tom natural de WhatsApp. "
            "Use só estes dados — não repita texto de sistema tipo 'Cliente usou'."
        )
        return facts
    return result


async def _transfer_and_notify_tool_failure(
    db: AsyncSession,
    conversation: Conversation,
    tools_by_key: dict[str, Tool],
    internal_reason: str,
) -> str:
    transfer_tool = tools_by_key.get(TOOL_KEY_TRANSFER)
    if (
        transfer_tool
        and transfer_tool.webhook_url
        and conversation.status != "with_human"
    ):
        await execute_tool(
            db,
            transfer_tool,
            {"motivo": internal_reason},
            conversation,
        )
    else:
        logger.warning(
            "Ferramenta falhou (%s) mas transferir_atendimento indisponível na conversa %s",
            internal_reason,
            conversation.id,
        )
    return _CUSTOMER_TRANSFER_ON_TOOL_FAILURE


def _format_tool_result_as_reply(result: dict) -> str | None:
    """Texto pro cliente. Retorna None se falha técnica — caller deve transferir."""
    if result.get("sucesso"):
        msg = str(result.get("mensagem") or "").strip()
        if msg:
            return msg
        dados = result.get("dados")
        if isinstance(dados, dict) and dados.get("mensagem"):
            return str(dados["mensagem"]).strip()
        return "Consultei no sistema — posso ajudar com mais alguma coisa?"
    if _is_technical_tool_failure(result):
        return None
    msg = str(result.get("mensagem") or "").strip()
    if msg:
        return msg
    return None


def _is_guest_tool(tool: Tool) -> bool:
    haystack = _normalize_tokens(f"{tool.tool_key} {tool.name or ''}")
    return bool(haystack & {"convidado", "convidados", "convite", "convites", "guest"})


def _is_schedule_tool(tool: Tool) -> bool:
    if tool.tool_key == TOOL_KEY_CHECK_SCHEDULE:
        return True
    haystack = _normalize_tokens(f"{tool.tool_key} {tool.name or ''}")
    return bool(haystack & {"agendamento", "agendar", "horarios", "horario", "schedule"})


def _guest_names_from_dados(dados: dict) -> list[str]:
    raw = dados.get("convidados_do_mes") or dados.get("convidados") or []
    names: list[str] = []
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    if not isinstance(raw, list):
        return names
    for item in raw:
        if isinstance(item, str) and item.strip():
            names.append(item.strip())
        elif isinstance(item, dict):
            name = item.get("nome") or item.get("name")
            if name:
                names.append(str(name).strip())
    return names


def _format_guest_reply_from_dados(dados: dict, *, who_question: bool = False) -> str:
    """Monta resposta humana (2ª pessoa) só a partir dos dados — ignora mensagem do n8n."""
    names = _guest_names_from_dados(dados)
    limite = dados.get("limite_mensal")
    usados = dados.get("convites_usados", 0)
    restantes = dados.get("convites_restantes")
    if restantes is None and limite is not None:
        restantes = max(0, int(limite) - int(usados))

    if who_question:
        if not names:
            return "Consultei aqui e não encontrei convidados registrados no seu nome neste mês."
        if len(names) == 1:
            return f"Este mês você levou *{names[0]}* como convidado. 😊"
        listed = "\n".join(f"• {name}" for name in names)
        return f"Este mês você levou estes convidados:\n{listed}"

    if limite is None:
        return "Consultei seus convites, mas não recebi o limite da unidade agora."

    usados_int = int(usados)
    limite_int = int(limite)
    restantes_int = int(restantes or 0)

    if usados_int == 0:
        return (
            f"Consultei aqui! 😊 Você ainda não trouxe convidados neste mês — "
            f"pode levar até *{limite_int}* convite(s)."
        )

    if names:
        if len(names) == 1:
            corpo = f"Você já trouxe *{names[0]}*"
        else:
            corpo = "Você já trouxe:\n" + "\n".join(f"• *{n}*" for n in names)
    else:
        corpo = f"Você já usou *{usados_int}* convite(s)"

    if restantes_int > 0:
        return (
            f"Consultei aqui! 😊 {corpo} — são *{usados_int}* de *{limite_int}* convites do mês. "
            f"Ainda pode trazer mais *{restantes_int}*. 😊"
        )
    return (
        f"Consultei aqui! 😊 {corpo} — você já usou os *{limite_int}* convites permitidos neste mês. "
        "Quando virar o mês, libera de novo."
    )


def _format_guest_tool_reply(result: dict, *, who_question: bool = False) -> str | None:
    """Fallback determinístico — prioriza dados estruturados, não a mensagem do n8n."""
    if not result.get("sucesso"):
        return _format_tool_result_as_reply(result)
    dados = result.get("dados")
    if isinstance(dados, dict) and dados:
        return _format_guest_reply_from_dados(dados, who_question=who_question)
    return _format_tool_result_as_reply(result)


def _tool_facts_for_llm(result: dict, tool: Tool) -> dict:
    """Fatores objetivos pra IA redigir — sem texto robótico tipo 'Cliente usou...'."""
    payload: dict = {"sucesso": bool(result.get("sucesso"))}
    dados = result.get("dados")
    if isinstance(dados, dict) and dados:
        payload["dados"] = dados
    if not _is_guest_tool(tool) and not _is_schedule_tool(tool):
        msg = str(result.get("mensagem") or "").strip()
        if msg and "cliente usou" not in _normalize_text(msg):
            payload["resumo_sistema"] = msg
    return payload


def _format_schedule_reply_from_dados(dados: dict) -> str:
    """Fallback humano para horários de avaliação física."""
    horarios = dados.get("horarios_disponiveis") or []
    data_fmt = dados.get("data_formatada") or dados.get("data") or "esse dia"
    unidade = dados.get("unidade") or "sua unidade"
    tipo = dados.get("tipo_agendamento") or "avaliação física"
    period_label = dados.get("periodo_solicitado_label") or _period_label(
        dados.get("periodo_solicitado")
    )
    pref_indisponivel = dados.get("horario_preferido_indisponivel")

    if not horarios:
        periodo_txt = f" no período da *{period_label}*" if period_label else ""
        return (
            f"Consultei aqui! 😊 No dia *{data_fmt}*{periodo_txt}, na unidade *{unidade}*, "
            f"não encontrei horários livres para *{tipo}*. Quer tentar outro dia ou período?"
        )

    shown = horarios[:12]
    lista = ", ".join(f"*{h}*" for h in shown)
    extra = ""
    if len(horarios) > len(shown):
        extra = f" (e mais {len(horarios) - len(shown)} horários)"

    intro = "Consultei aqui! 😊 "
    if pref_indisponivel:
        intro += (
            f"O horário *{pref_indisponivel}* não está livre, mas "
        )
    periodo_txt = f" de *{period_label}*" if period_label else ""
    return (
        f"{intro}No dia *{data_fmt}*{periodo_txt}, na *{unidade}*, "
        f"estes horários estão livres para *{tipo}*: {lista}{extra}. "
        "Qual prefere?"
    )


async def _humanize_tool_reply_with_llm(
    config: AiConfig,
    user_text: str,
    tool_result: dict,
    tool: Tool,
    lead: Lead | None,
    *,
    who_question: bool = False,
) -> str:
    """A IA redige a resposta pro cliente a partir dos dados da ferramenta."""
    api_key = decrypt_secret(config.llm_api_key_encrypted)
    facts = _tool_facts_for_llm(tool_result, tool)
    ai_name = config.ai_name or "Mônica"
    first_name = (lead.name or "").split()[0] if lead and lead.name else None

    system = (
        f"{compose_base_prompt(config)}\n\n"
        f"Você é a {ai_name}. Uma consulta no sistema acabou de retornar os dados abaixo.\n"
        "Escreva UMA resposta curta pro WhatsApp, informando o cliente de forma natural — "
        "fale direto com ELE/ELA (segunda pessoa: *você*), NUNCA diga 'Cliente', 'o cliente' "
        "ou texto de relatório de sistema.\n"
        "Use só números e nomes que estão no JSON. Não invente nada. "
        "Tom caloroso de atendente humano, 2–4 frases. Pode usar *negrito* com um asterisco."
    )
    if _is_guest_tool(tool):
        system += (
            "\nAssunto: convites/convidados do mês. Informe quantos já usou, quantos restam "
            "e, se houver convidados_do_mes, cite o(s) nome(s) de forma natural."
        )
    if who_question and _is_guest_tool(tool):
        system += "\nO cliente quer saber QUEM já levou — foque nos nomes em convidados_do_mes."
    if _is_schedule_tool(tool):
        system += (
            "\nAssunto: horários livres para avaliação física. Informe a data, a unidade "
            "e liste os horários de horarios_disponiveis de forma clara. "
            "Se houver periodo_solicitado_label (manhã/tarde), deixe claro que a lista "
            "é desse período. Se horario_preferido_indisponivel existir, diga gentilmente "
            "que aquele horário não está livre e mostre as alternativas do mesmo período. "
            "Pergunte qual horário prefere — NÃO diga que já agendou."
        )
    if first_name:
        system += f"\nPode chamar o cliente de {first_name}."

    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                f"Pergunta do cliente:\n{user_text}\n\n"
                f"Dados da consulta (JSON):\n{_safe_tool_result_json(facts)}"
            ),
        },
    ]
    try:
        assistant = await chat_completion(
            provider=config.llm_provider,
            model=config.llm_model,
            api_key=api_key,
            messages=messages,
            temperature=config.temperature,
            tools=None,
        )
        text = (assistant.get("content") or "").strip()
        if text:
            return text
    except Exception:  # noqa: BLE001
        logger.exception(
            "Falha ao humanizar resposta da ferramenta %s — usando fallback determinístico",
            tool.tool_key,
        )

    if _is_guest_tool(tool) and isinstance(facts.get("dados"), dict):
        return _format_guest_reply_from_dados(facts["dados"], who_question=who_question)
    if _is_schedule_tool(tool) and isinstance(facts.get("dados"), dict):
        return _format_schedule_reply_from_dados(facts["dados"])
    fallback = _format_tool_result_as_reply(tool_result)
    if fallback and "cliente" not in _normalize_text(fallback):
        return fallback
    return "Consultei no sistema — já te confirmo a informação. 😊"


def _is_guest_who_followup(
    text: str,
    recent_customer_texts: list[str] | None,
    lead: Lead | None,
) -> bool:
    """Ex.: 'Quem que eu levei?' depois de consultar convidados."""
    tokens = _normalize_tokens(text)
    if "quem" not in tokens and "nome" not in tokens:
        return False
    if not tokens & {"levei", "levou", "troux", "convidado", "convidados", "convite", "convites", "nomes"}:
        return False
    if lead and (lead.is_student or lead.unit) and lead.cpf:
        return True
    prior = [t for t in (recent_customer_texts or []) if t and t.strip() != (text or "").strip()]
    return any(
        _is_guest_operational_check(t, lead)
        or _student_operational_action(t, lead)
        for t in prior[-8:]
    )


def _pick_student_operational_tool(
    user_text: str,
    tools_by_key: dict[str, Tool],
    recent_customer_texts: list[str] | None = None,
) -> Tool | None:
    """Escolhe a ferramenta operacional (convidados, parcelas…) pelo assunto."""
    candidates = [user_text]
    if recent_customer_texts:
        prior = [t for t in recent_customer_texts if t and t.strip() != (user_text or "").strip()]
        candidates.extend(reversed(prior[-5:]))
    for text in candidates:
        picked = _pick_student_operational_tool_from_text(text, tools_by_key)
        if picked:
            return picked
    return None


def _pick_student_operational_tool_from_text(user_text: str, tools_by_key: dict[str, Tool]) -> Tool | None:
    tokens = _normalize_tokens(user_text)
    wants_guests = bool(tokens & {"convidado", "convidados", "convite", "convites"}) or (
        "quem" in tokens and tokens & {"levei", "levou", "troux", "convidado", "convite", "nome", "nomes"}
    )
    wants_billing = bool(
        tokens & {"parcela", "parcelas", "atrasad", "atraso", "inadimpl", "boleto", "boletos", "carne", "multa"}
    )
    if not wants_guests and not wants_billing:
        return None

    scored: list[tuple[int, Tool]] = []
    for key, tool in tools_by_key.items():
        if key in _INTERNAL_OR_GENERIC_TOOL_KEYS or not tool.webhook_url:
            continue
        if not _is_valid_openai_tool(tool):
            continue
        haystack = _normalize_tokens(f"{key} {tool.name or ''}")
        score = 0
        if wants_guests and haystack & {"convidado", "convidados", "convite", "convites", "guest"}:
            score += 10
        if wants_billing and haystack & {"parcela", "parcelas", "atrasad", "atraso", "inadimpl", "boleto", "carne"}:
            score += 10
        if score:
            scored.append((score, tool))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _student_operational_followup(
    text: str,
    recent_customer_texts: list[str] | None,
    lead: Lead | None = None,
) -> bool:
    """CPF ou confirmação de aluno depois de dúvida operacional ou informativa."""
    if _physical_eval_in_recent(recent_customer_texts):
        return False
    prior = [t for t in (recent_customer_texts or []) if t and t.strip() != (text or "").strip()]
    if _confirms_is_student(text) and any(_is_guest_info_question(t, lead) for t in prior[-5:]):
        return True
    if not _extract_cpf_from_text(text) or _student_operational_action(text, lead):
        return False
    return any(
        _is_guest_info_question(t, lead) or _student_operational_action(t, lead) for t in prior[-5:]
    )


async def _run_student_operational_pipeline(
    db: AsyncSession,
    conversation: Conversation,
    user_text: str,
    tools_by_key: dict[str, Tool],
    lead: Lead | None,
    *,
    config: AiConfig,
    is_first_contact: bool,
    ai_name: str,
    recent_customer_texts: list[str] | None = None,
) -> tuple[str | None, Lead | None]:
    """Rede de segurança: ação de aluno nunca pede unidade — só CPF, depois
    verificar_unidade_por_cpf e a ferramenta específica do pedido."""
    verify_tool = tools_by_key.get(TOOL_KEY_VERIFY_UNIT_BY_CPF)
    if not verify_tool:
        return None, lead

    cpf = (lead.cpf if lead and lead.cpf else None) or _extract_cpf_from_text(user_text)
    if cpf and (not lead or not lead.cpf):
        lead = await _upsert_lead(
            db, conversation.company_id, conversation.contact_phone, {"cpf": cpf}
        ) or lead

    if not cpf:
        intro = (
            f"Olá! Eu sou a {ai_name}, assistente virtual da Mov Fit. "
            if is_first_contact
            else ""
        )
        return (
            f"{intro}Para consultar isso no sistema, preciso do seu *CPF* (só os números). "
            "Pode me enviar? 😊"
        ), lead

    unit = lead.unit if lead else None
    if not unit:
        verify_result = await execute_tool(db, verify_tool, {"cpf": cpf}, conversation)
        if not verify_result.get("sucesso"):
            reply = _format_tool_result_as_reply(verify_result)
            if reply is None:
                reply = await _transfer_and_notify_tool_failure(
                    db, conversation, tools_by_key, "verificar_unidade_por_cpf falhou"
                )
            return reply, lead
        lead_result = await db.execute(
            select(Lead).where(
                Lead.company_id == conversation.company_id,
                Lead.phone == sanitize_phone_digits(conversation.contact_phone),
            )
        )
        lead = lead_result.scalar_one_or_none() or lead
        unit = lead.unit if lead else None
        dados = verify_result.get("dados")
        if not unit and isinstance(dados, dict):
            unit = dados.get("unidade")
        if not unit:
            return (
                "Não encontrei matrícula ativa com esse CPF. "
                "Confere se digitou certinho ou quer falar com um atendente?"
            ), lead

    op_tool = _pick_student_operational_tool(
        user_text, tools_by_key, recent_customer_texts=recent_customer_texts
    )
    if not op_tool:
        return None, lead

    declared_params = {
        p.get("name")
        for p in (op_tool.parameters or [])
        if isinstance(p, dict) and p.get("name")
    }
    args: dict = {"cpf": cpf}
    if unit:
        if "unidade" in declared_params:
            args["unidade"] = unit
        if "Unidade" in declared_params:
            args["Unidade"] = unit

    op_result = await execute_tool(db, op_tool, args, conversation)
    who_question = _is_guest_who_followup(user_text, recent_customer_texts, lead)
    if op_result.get("sucesso"):
        reply = await _humanize_tool_reply_with_llm(
            config,
            user_text,
            op_result,
            op_tool,
            lead,
            who_question=who_question,
        )
    elif _is_guest_tool(op_tool):
        reply = _format_guest_tool_reply(op_result, who_question=who_question)
    else:
        reply = _format_tool_result_as_reply(op_result)
    if reply is None:
        reply = await _transfer_and_notify_tool_failure(
            db, conversation, tools_by_key, f"ferramenta {op_tool.tool_key} falhou"
        )
    return reply, lead


async def _run_physical_eval_pipeline(
    db: AsyncSession,
    conversation: Conversation,
    user_text: str,
    tools_by_key: dict[str, Tool],
    lead: Lead | None,
    *,
    config: AiConfig,
    is_first_contact: bool,
    ai_name: str,
    recent_customer_texts: list[str] | None = None,
) -> tuple[str | None, Lead | None]:
    """Avaliação física: CPF → verificar_unidade_por_cpf → dia → consultar horários."""
    verify_tool = tools_by_key.get(TOOL_KEY_VERIFY_UNIT_BY_CPF)
    schedule_tool = tools_by_key.get(TOOL_KEY_CHECK_SCHEDULE)
    if not verify_tool or not schedule_tool:
        return None, lead

    brazil_now = datetime.now(timezone(timedelta(hours=-3)))

    cpf = (lead.cpf if lead and lead.cpf else None) or _extract_cpf_from_text(user_text)
    if cpf and (not lead or not lead.cpf):
        lead = await _upsert_lead(
            db, conversation.company_id, conversation.contact_phone, {"cpf": cpf}
        ) or lead

    if not cpf:
        intro = (
            f"Olá! Eu sou a {ai_name}, assistente virtual da Mov Fit. "
            if is_first_contact
            else ""
        )
        return (
            f"{intro}Para agendar sua *avaliação física*, preciso do seu *CPF* "
            "(só os números). Pode me enviar? 😊"
        ), lead

    unit = lead.unit if lead else None
    if not unit:
        verify_result = await execute_tool(db, verify_tool, {"cpf": cpf}, conversation)
        if not verify_result.get("sucesso"):
            reply = _format_tool_result_as_reply(verify_result)
            if reply is None:
                reply = await _transfer_and_notify_tool_failure(
                    db, conversation, tools_by_key, "verificar_unidade_por_cpf falhou"
                )
            return reply, lead
        lead_result = await db.execute(
            select(Lead).where(
                Lead.company_id == conversation.company_id,
                Lead.phone == sanitize_phone_digits(conversation.contact_phone),
            )
        )
        lead = lead_result.scalar_one_or_none() or lead
        unit = lead.unit if lead else None
        dados = verify_result.get("dados")
        if not unit and isinstance(dados, dict):
            unit = dados.get("unidade")
        if not unit:
            return (
                "Não encontrei matrícula ativa com esse CPF. "
                "Confere se digitou certinho ou quer falar com um atendente?"
            ), lead

    pref = _physical_eval_schedule_preference(
        user_text, recent_customer_texts, brazil_now
    )
    schedule_date = pref["date"]
    if not schedule_date:
        return _physical_eval_ask_day_reply(unit, lead), lead

    if _schedule_date_is_weekend(schedule_date):
        return _physical_eval_weekend_reply(schedule_date, lead), lead

    declared_params = {
        p.get("name")
        for p in (schedule_tool.parameters or [])
        if isinstance(p, dict) and p.get("name")
    }
    args: dict = {}
    if "unidade" in declared_params:
        args["unidade"] = unit
    if "Unidade" in declared_params:
        args["Unidade"] = unit
    if "data" in declared_params:
        args["data"] = schedule_date
    if "dia" in declared_params:
        args["dia"] = schedule_date

    schedule_result = await execute_tool(db, schedule_tool, args, conversation)
    if schedule_result.get("sucesso"):
        schedule_result = _enrich_schedule_result_with_preference(
            schedule_result,
            period=pref["period"],
            preferred_time=pref["preferred_time"],
        )
        horarios = (schedule_result.get("dados") or {}).get("horarios_disponiveis") or []
        if not horarios:
            reply = _physical_eval_no_slots_reply(
                schedule_date, pref["period"], lead
            )
        else:
            reply = await _humanize_tool_reply_with_llm(
                config,
                user_text,
                schedule_result,
                schedule_tool,
                lead,
            )
    else:
        reply = _format_tool_result_as_reply(schedule_result)
        if reply is None:
            reply = await _transfer_and_notify_tool_failure(
                db,
                conversation,
                tools_by_key,
                f"ferramenta {schedule_tool.tool_key} falhou",
            )
    return reply, lead


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


_PLAN_CAPTION_MARKERS = ("🏋️", "Faça sua matrícula", "Link de cadastro", "PLANO ANUAL", "PLANO MENSAL")
_HISTORY_LLM_MAX_CHARS = 700


def _history_text_for_llm(message: Message) -> str | None:
    """Encurta legendas gigantes de plano no histórico — repetir 2–3 descrições
    completas a cada turno estourava contexto e a OpenAI passava a devolver
    content vazio (cliente ficava sem resposta)."""
    text = message.text
    if not text:
        return None
    if message.content_type == "image":
        return text
    if message.actor == "ai" and len(text) > _HISTORY_LLM_MAX_CHARS:
        if any(marker in text for marker in _PLAN_CAPTION_MARKERS):
            return "[Descrição completa do plano já enviada ao cliente por imagem/mensagem anterior.]"
    return text


def _safe_tool_result_json(result: dict) -> str:
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return json.dumps(
            {"sucesso": False, "mensagem": "Erro interno ao processar resultado da ferramenta."},
            ensure_ascii=False,
        )


async def _count_ai_messages(db: AsyncSession, conversation_id: UUID) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(Message)
        .where(Message.conversation_id == conversation_id, Message.actor == "ai")
    )
    return int(result.scalar() or 0)


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
    elif tool.tool_key == TOOL_KEY_CHECK_SCHEDULE:
        description = (
            description + " NÃO chame direto sem CPF e unidade do aluno — o fluxo correto é: "
            "pedir CPF, usar verificar_unidade_por_cpf, pedir o dia, então chamar esta ferramenta "
            "com unidade (nome exato) e data (yyyyMMdd). Só LISTA horários — não confirma agendamento."
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
        lead_result_fields = _sanitize_lead_fields(
            {k: v for k, v in {**data, **dados_extra}.items() if k in _LEAD_ARG_KEYS and v}
        )
        if lead_result_fields:
            await _upsert_lead(db, conversation.company_id, conversation.contact_phone, lead_result_fields)

        # Unidade do aluno confirmada: (a) tool mandou unidade+cpf juntos
        # nos argumentos (ex: consultar parcela), ou (b) a tool devolveu
        # unidade em dados (ex: verificar_unidade_por_cpf). Nos dois casos
        # grava no Lead + is_student — reforço no código, não só prompt.
        unidade_confirmada = None
        cpf_ref = _sanitize_cpf(
            arguments.get("cpf") or dados_extra.get("cpf") or data.get("cpf")
        )
        arg_cpf = _sanitize_cpf(arguments.get("cpf"))
        if arguments.get("unidade") and arg_cpf:
            unidade_confirmada = arguments["unidade"]
        elif dados_extra.get("unidade") and (
            cpf_ref or tool.tool_key == TOOL_KEY_VERIFY_UNIT_BY_CPF
        ):
            unidade_confirmada = dados_extra["unidade"]
        if unidade_confirmada and not _looks_like_unresolved_template(unidade_confirmada):
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

    lead_result = await db.execute(
        select(Lead).where(
            Lead.company_id == conversation.company_id,
            Lead.phone == sanitize_phone_digits(conversation.contact_phone),
        )
    )
    lead = lead_result.scalar_one_or_none()
    confirmed_student = bool(lead and (lead.is_student or lead.unit))
    student_operational = _student_operational_action(user_text, lead)
    guest_info_question = _is_guest_info_question(user_text, lead)

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

    guest_who_followup = _is_guest_who_followup(user_text, recent_customer_texts, lead)
    physical_eval_intent = _is_physical_eval_intent(user_text)
    physical_eval_active = (
        has_verify_unit_tool
        and TOOL_KEY_CHECK_SCHEDULE in tools_by_key
        and not plan_intent_active
        and (
            physical_eval_intent
            or _physical_eval_followup(user_text, recent_customer_texts, lead)
        )
    )
    if physical_eval_active:
        pipeline_reply, lead = await _run_physical_eval_pipeline(
            db,
            conversation,
            user_text,
            tools_by_key,
            lead,
            config=config,
            is_first_contact=is_first_contact,
            ai_name=ai_name,
            recent_customer_texts=recent_customer_texts,
        )
        if pipeline_reply is not None:
            return pipeline_reply, None, False

    student_pipeline_active = has_verify_unit_tool and (
        ((student_operational or guest_who_followup) and not plan_intent_active)
        or _student_operational_followup(user_text, recent_customer_texts, lead)
    )
    if student_pipeline_active:
        pipeline_reply, lead = await _run_student_operational_pipeline(
            db,
            conversation,
            user_text,
            tools_by_key,
            lead,
            config=config,
            is_first_contact=is_first_contact,
            ai_name=ai_name,
            recent_customer_texts=recent_customer_texts,
        )
        if pipeline_reply is not None:
            return pipeline_reply, None, False

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
    messages.append(
        {
            "role": "system",
            "content": (
                "Ferramentas e RAG: se uma ferramenta não existir, falhar (sucesso=false) "
                "ou não retornar dados úteis, NÃO fique em silêncio — responda em texto "
                "usando a base de conhecimento (RAG) e o histórico desta conversa. Se "
                "ainda não tiver a informação, diga honestamente que não conseguiu "
                "verificar no sistema agora e peça para reformular ou ofereça transferir "
                "pra um atendente. Perguntas simples (horário, endereço, estacionamento, "
                "estrutura) devem ser respondidas pelo RAG/histórico — não peça CPF nem "
                "chame ferramentas de aluno só por causa disso."
            ),
        }
    )
    if _text_has_non_plan_topic(user_text):
        messages.append(
            {
                "role": "system",
                "content": (
                    "NESTE TURNO o assunto é informativo (horário, endereço, estacionamento, "
                    "estrutura etc.) — responda com RAG e o que já foi dito nesta conversa "
                    "(incluindo unidade já mencionada). Não mude de assunto pra planos nem "
                    "peça CPF. Sempre mande texto pro cliente neste turno."
                ),
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
                    "O cliente perguntou sobre convidados de forma GERAL — pode ser curioso "
                    "querendo saber a política da academia (ex: quantos convidados pode levar "
                    "ao se cadastrar), ainda NÃO confirmado como aluno matriculado. "
                    "Responda com a política/regras da base de conhecimento (RAG): como "
                    "funciona levar convidados, limites gerais da academia, etc. "
                    "NÃO peça CPF nem unidade neste turno — em vez disso, pergunte se ele "
                    "*já é aluno matriculado*. Só se confirmar que já é aluno e quiser "
                    "consultar quantos convites *dele* ainda tem no mês é que você pede "
                    "SOMENTE o CPF e usa as ferramentas. NUNCA fique sem responder."
                ),
            }
        )
    elif physical_eval_intent and has_verify_unit_tool:
        messages.append(
            {
                "role": "system",
                "content": (
                    "NESTE TURNO o cliente quer agendar/consultar horários de AVALIAÇÃO FÍSICA. "
                    "Fluxo: (1) peça SOMENTE o CPF se ainda não tiver; (2) chame "
                    "verificar_unidade_por_cpf — NÃO peça a unidade (use dados.nome se vier); "
                    "(3) peça dia ÚTIL + preferência de manhã/tarde — ex: *segunda de manhã*, "
                    "*terça às 9h*, *quarta à tarde*; avaliação NÃO ocorre sábado/domingo; "
                    "(4) chame consultar_agendamento_horarios com unidade + data (yyyyMMdd); "
                    "filtre mentalmente manhã (antes de 12h) e tarde (12h+); se o horário "
                    "exato não existir, ofereça outros do mesmo período; se o cliente mudar "
                    "só o dia (ex: 'então terça'), mantenha o período que ele pediu antes; "
                    "NÃO confirme agendamento neste fluxo. Use o primeiro nome quando souber."
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
    elif student_operational and not confirmed_student and not has_verify_unit_tool:
        messages.append(
            {
                "role": "system",
                "content": (
                    "NESTE TURNO o cliente pediu algo que normalmente exige consulta no sistema "
                    "(parcelas, convidados do mês etc.), mas as ferramentas de aluno NÃO estão "
                    "disponíveis nesta conversa. Responda com o que souber da base de "
                    "conhecimento; se não puder consultar o sistema, explique isso claramente "
                    "e ofereça transferir — nunca fique em silêncio."
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
        content = _history_text_for_llm(m)
        if content:
            messages.append({"role": role, "content": content})

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

    try:
        assistant_message = await chat_completion(
            provider=config.llm_provider,
            model=config.llm_model,
            api_key=api_key,
            messages=messages,
            temperature=config.temperature,
            tools=tool_defs,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "OpenAI falhou ao gerar resposta da conversa %s (texto do cliente: %r)",
            conversation.id,
            user_text[:200],
        )
        return (
            "Desculpe, tive uma dificuldade técnica momentânea. Pode repetir sua pergunta? 🙏",
            None,
            False,
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
                    declared_params = {
                        p.get("name")
                        for p in (autofill_tool.parameters or [])
                        if isinstance(p, dict) and p.get("name")
                    }
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
                lead_fields = _sanitize_lead_fields(
                    {k: v for k, v in arguments.items() if k in _LEAD_ARG_KEYS and v}
                )
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
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "content": _safe_tool_result_json(result),
                    }
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
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "content": _safe_tool_result_json(result),
                    }
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
                elif (
                    _is_technical_tool_failure(result)
                    and key != TOOL_KEY_TRANSFER
                ):
                    customer_msg = await _transfer_and_notify_tool_failure(
                        db,
                        conversation,
                        tools_by_key,
                        f"ferramenta {key} falhou no loop da IA",
                    )
                    return customer_msg, deferred_end_call, bool(touched_units)
                result = _tool_result_for_llm(result, tool)
            else:
                result = {"sucesso": False, "mensagem": f"Ferramenta '{key}' não encontrada."}
                if key != TOOL_KEY_TRANSFER:
                    customer_msg = await _transfer_and_notify_tool_failure(
                        db,
                        conversation,
                        tools_by_key,
                        f"ferramenta {key} não encontrada",
                    )
                    return customer_msg, deferred_end_call, bool(touched_units)

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": _safe_tool_result_json(result),
                }
            )

        try:
            assistant_message = await chat_completion(
                provider=config.llm_provider,
                model=config.llm_model,
                api_key=api_key,
                messages=messages,
                temperature=config.temperature,
                tools=tool_defs,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "OpenAI falhou no loop de ferramentas da conversa %s",
                conversation.id,
            )
            return (
                "Desculpe, tive uma dificuldade técnica momentânea. Pode repetir sua pergunta? 🙏",
                deferred_end_call,
                bool(touched_units),
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
    outbound_before = await _count_ai_messages(db, conversation.id)
    deferred_end_call: tuple[Tool, dict] | None = None
    delivered_via_tools = False
    try:
        reply, deferred_end_call, delivered_via_tools = await asyncio.wait_for(
            generate_ai_reply(db, conversation, combined_text),
            timeout=90.0,
        )
    except asyncio.TimeoutError:
        logger.error(
            "Timeout (90s) ao gerar resposta da conversa %s (pendente: %r)",
            conversation_id,
            combined_text[:300],
        )
        reply = None
        deferred_end_call = None
        delivered_via_tools = False
    except Exception:  # noqa: BLE001
        logger.exception(
            "Exceção ao gerar resposta da conversa %s (pendente: %r)",
            conversation_id,
            combined_text[:300],
        )
        reply = None

    outbound_after = await _count_ai_messages(db, conversation.id)
    if outbound_after > outbound_before:
        delivered_via_tools = True

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
            logger.error(
                "Resposta vazia da IA na conversa %s — cliente ficaria sem resposta. "
                "Pendente: %r",
                conversation_id,
                combined_text[:300],
            )
            reply = (
                "Desculpe, não consegui processar sua mensagem agora. "
                "Pode reformular ou repetir a pergunta? 🙏"
            )

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
        # No chat de teste não espera entre bolhas — libera o lock mais cedo
        # pro próximo turno do cliente (visto travar follow-up tipo estacionamento).
        if not is_test and i < len(bubbles) - 1:
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
