from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from datetime import date, datetime, timezone
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
    Message,
    MetricsDaily,
    Number,
    Plan,
    RagSource,
    Tool,
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


def _normalize_tokens(text: str) -> set[str]:
    """Minúsculo, sem acento, só letras/números — pra comparar 'Santarém' com
    'santarem' e 'MOVFIT Santarém — Premium (24h)' com 'Santarém - 24 horas'
    mesmo quando a IA parafraseia em vez de copiar o nome literal."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return set(re.findall(r"[a-z0-9]+", ascii_text.lower()))


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
            images.append({"unidade": unit.name, "plano": plan.name, "url": plan.image_url})
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


def _mentions_any_plan(text_tokens: set[str], plans: list[Plan]) -> bool:
    """Verdadeiro quando o texto (tipicamente a própria resposta da IA) cita
    o nome de algum plano por extenso — sinal confiável de que o assunto é
    mesmo plano, sem depender do cliente ter digitado o nome da unidade."""
    for plan in plans:
        name_tokens = _normalize_tokens(plan.name)
        if name_tokens and name_tokens <= text_tokens:
            return True
    return False


def _tool_to_openai_schema(tool: Tool) -> dict:
    properties: dict = {}
    required: list[str] = []
    for p in tool.parameters or []:
        properties[p["name"]] = {
            "type": p.get("type") or "string",
            "description": p.get("description") or "",
        }
        if p.get("required"):
            required.append(p["name"])
    return {
        "type": "function",
        "function": {
            "name": tool.tool_key,
            "description": tool.description or tool.name,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


async def execute_tool(
    db: AsyncSession,
    tool: Tool,
    arguments: dict,
    conversation: Conversation,
    force_resend: bool = False,
    touched_units: set[str] | None = None,
) -> dict:
    if not tool.webhook_url:
        return {"sucesso": False, "mensagem": "Ferramenta sem webhook configurado."}

    contexto = {
        "telefone_cliente": sanitize_phone_digits(conversation.contact_phone),
        "nome_cliente": conversation.contact_name,
        "conversation_id": str(conversation.id),
    }
    plan_images: list[dict] = []
    if tool.tool_key == TOOL_KEY_SEND_PLAN_IMAGES:
        resolved_images = await _resolve_plan_images(db, conversation.company_id, arguments)
        if touched_units is not None:
            # Registra a unidade como "atendida nesta resposta" mesmo se o
            # dedup abaixo acabar não mandando nada — evita que a chamada da
            # IA e o gatilho automático mandem a mesma imagem duas vezes na
            # mesma resposta quando o cliente pede reenvio explícito.
            touched_units.update(img["unidade"] for img in resolved_images)
        if force_resend:
            plan_images = resolved_images
        else:
            already_sent = await _get_sent_plan_image_urls(db, conversation.id)
            plan_images = [img for img in resolved_images if img["url"] not in already_sent]
        contexto["imagens_planos"] = plan_images
        logger.info(
            "enviar_imagens_planos: arguments=%r resolved=%d imagens (force_resend=%s)",
            arguments,
            len(plan_images),
            force_resend,
        )
        if resolved_images and not plan_images:
            # Achou o(s) plano(s), mas a imagem de cada um já foi mandada
            # antes nesta conversa e o cliente não pediu reenvio — não vale
            # nem chamar o webhook de novo.
            return {"sucesso": True, "mensagem": "Imagens já enviadas anteriormente nesta conversa.", "dados": {}}

    payload = {
        "ferramenta": tool.tool_key,
        "argumentos": arguments,
        "contexto": contexto,
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(tool.webhook_url, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Ferramenta %s (%s) falhou: %s", tool.name, tool.tool_key, exc)
        return {"sucesso": False, "mensagem": "Falha ao executar a ferramenta agora."}

    tool.last_executed_at = datetime.now(timezone.utc)
    logger.info("Ferramenta %s (%s) respondeu: %r", tool.name, tool.tool_key, data)

    if data.get("sucesso"):
        if tool.tool_key == TOOL_KEY_TRANSFER:
            conversation.ai_enabled = False
            conversation.status = "with_human"
        elif tool.tool_key == TOOL_KEY_END:
            conversation.status = "resolved"

    is_test = conversation.channel == "test_console"
    if tool.tool_key == TOOL_KEY_SEND_PLAN_IMAGES and plan_images and (data.get("sucesso") or is_test):
        # Quem manda a mídia de verdade é o workflow n8n (WhatsApp/Evolution
        # etc), e só sabemos que a entrega real aconteceu se ele responder
        # sucesso:true conforme o contrato. No chat de teste não existe
        # entrega real pra confirmar — mostra o que seria enviado mesmo assim,
        # pra dar pra validar se a IA escolheu as imagens certas mesmo antes
        # do workflow n8n devolver a resposta no formato esperado.
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
                text=" · ".join(f"{img['plano']} ({img['unidade']})" for img in plan_images),
                timestamp=datetime.now(timezone.utc),
                actor="ai",
                raw_payload={"images": plan_images},
            ),
        )

    return data


async def _auto_send_plan_images(
    db: AsyncSession,
    conversation: Conversation,
    user_text: str,
    assistant_text: str,
    tools_by_key: dict[str, Tool],
    touched_units: set[str],
) -> None:
    """Rede de segurança: garante que a imagem do plano seja mandada quando o
    cliente menciona uma unidade, mesmo que a IA não decida chamar a
    ferramenta por conta própria (comportamento de prompt não é 100%
    consistente). O dedup ("não repetir a mesma imagem") e o reenvio quando
    o cliente pede explicitamente já são tratados dentro do execute_tool,
    então essa função só decide QUANDO tentar chamar, não SE deve repetir.

    `touched_units` traz as unidades que a própria IA já mandou imagem nessa
    mesma resposta (via chamada de ferramenta) — pula essas pra não mandar a
    mesma imagem duas vezes numa resposta só."""
    tool = tools_by_key.get(TOOL_KEY_SEND_PLAN_IMAGES)
    if not tool or not tool.webhook_url:
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
        # planos da unidade, e o dedup por URL abaixo (dentro do
        # execute_tool) já cuida de não repetir o que já foi enviado.
        return
    await execute_tool(
        db, tool, {"Unidade": unit.name}, conversation, force_resend=force_resend, touched_units=touched_units
    )


async def generate_ai_reply(
    db: AsyncSession,
    conversation: Conversation,
    user_text: str,
) -> str | None:
    if not conversation.ai_enabled:
        return None

    result = await db.execute(
        select(AiConfig)
        .options(selectinload(AiConfig.rag_sources))
        .where(AiConfig.company_id == conversation.company_id)
    )
    config = result.scalar_one_or_none()
    if not config or config.operation_mode == "off":
        return None
    if not config.llm_api_key_encrypted:
        logger.warning("Empresa %s sem API key LLM", conversation.company_id)
        return None

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

    ai_name = config.ai_name or "assistente virtual"
    # Permite usar {ai_name} no prompt pra sempre bater com o campo "Nome da
    # IA" — sem isso, quem trocasse o nome só nesse campo ficaria com um
    # prompt cujo texto continua citando o nome antigo escrito à mão.
    system_prompt = (config.system_prompt or "").replace("{ai_name}", ai_name)
    messages = [{"role": "system", "content": system_prompt}]
    messages.append(
        {
            "role": "system",
            "content": (
                "Formatação: isso vai pro WhatsApp, não markdown de verdade. Use *um "
                "asterisco* pra negrito (nunca **dois**) e _sublinhado_ pra itálico — sem "
                "cabeçalho tipo ### ou ##, sem \"**\" em lugar nenhum. Listas: hífen simples, "
                "sem numerar a menos que a ordem importe. Ao apresentar planos, não repita a "
                "descrição/slogan geral da academia em cada plano — isso já foi dito (ou nem "
                "precisa ser dito) uma vez só; cada plano é só nome, valor, fidelidade, "
                "benefícios e link, direto ao ponto."
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
        select(Unit.name).where(Unit.company_id == conversation.company_id, Unit.is_active.is_(True))
    )
    active_unit_names = [n for (n,) in units_result.all()]
    if len(active_unit_names) > 1:
        messages.append(
            {
                "role": "system",
                "content": (
                    "Esta academia tem HOJE, atualmente, estas unidades (lista sempre atual — "
                    "ignore qualquer outro nome de unidade citado antes nesta conversa, mesmo por "
                    "você mesma, se ele não estiver nesta lista): " + ", ".join(active_unit_names) + ". "
                    "Informações como horário de funcionamento, endereço, estrutura, estacionamento "
                    "e aulas variam de unidade para unidade. Se o cliente perguntar algo assim e "
                    "ainda não tiver dito nesta conversa qual unidade é a dele, PERGUNTE primeiro "
                    "qual unidade antes de responder — como um atendente humano faria. Nunca escolha "
                    "uma unidade por conta própria nem misture dados de unidades diferentes. Se o "
                    "cliente já informou a unidade antes na conversa, não pergunte de novo, use a "
                    "que ele já disse."
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
                    "Catálogo oficial de unidades e planos, sempre atualizado — use isso, "
                    "não invente valores fora daqui. Isso vale MAIS que qualquer coisa dita "
                    "antes nesta conversa (inclusive por você mesma): se uma unidade, plano ou "
                    "preço mencionado anteriormente não aparecer aqui embaixo, ele não existe "
                    "mais — não repita. Quando o cliente confirmar qual plano "
                    "específico ele quer (não só a unidade), e esse plano tiver um \"Link de "
                    "cadastro\", envie esse link exatamente como está aqui — não invente nem "
                    "reproduza de memória. Se o plano não tiver link, avise que vai encaminhar "
                    "para um atendente concluir a matrícula.\n" + catalog_context
                ),
            }
        )
    for m in history:
        role = "assistant" if m.actor in {"ai", "human_agent"} else "user"
        if m.text:
            messages.append({"role": role, "content": m.text})

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
    tool_defs = [_tool_to_openai_schema(t) for t in active_tools] or None

    assistant_message = await chat_completion(
        provider=config.llm_provider,
        model=config.llm_model,
        api_key=api_key,
        messages=messages,
        temperature=config.temperature,
        tools=tool_defs,
    )

    touched_units: set[str] = set()
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
        for call in assistant_message["tool_calls"]:
            fn = call.get("function") or {}
            key = fn.get("name")
            try:
                arguments = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}

            tool = tools_by_key.get(key)
            if tool:
                result = await execute_tool(
                    db,
                    tool,
                    arguments,
                    conversation,
                    force_resend=_wants_image_explicitly(user_text),
                    touched_units=touched_units,
                )
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
    await _auto_send_plan_images(db, conversation, user_text, final_text, tools_by_key, touched_units)
    return final_text


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
    reply = await generate_ai_reply(db, conversation, combined_text)
    if not reply:
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
