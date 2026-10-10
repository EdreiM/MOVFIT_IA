"""Integração com os relatórios da Pacto (servidor MCP com OAuth).

O que isso faz pela IA, sem ninguém atualizar base de conhecimento à mão:

- uma vez por dia busca, por unidade, os horários de maior e menor movimento
  (acessos de catraca dos últimos 30 dias) e guarda uma frase pronta, que
  vai pro prompt junto com o catálogo;
- quando o cliente pergunta se está cheio AGORA, consulta a lotação em tempo
  real da unidade na hora.

Como a conexão funciona: alguém clica em "Conectar" no painel e autoriza na
tela da Pacto (OAuth com PKCE, cliente registrado dinamicamente); o backend
guarda os tokens criptografados e renova o acesso sozinho. Se o acesso cair
(permissão removida, refresh expirado), a conexão vai pra "error" e o painel
pede pra reconectar — a IA simplesmente segue sem esses dados.

Tudo aqui é apoio: nenhuma falha da Pacto pode impedir a IA de responder.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import PactoConnection, PactoUnit, Unit
from app.security import decrypt_secret, encrypt_secret
from app.services.locks import LOCK_NAMESPACE_PACTO_SYNC, advisory_lock
from app.services.text_normalize import normalize_text, normalize_tokens

logger = logging.getLogger(__name__)

MCP_URL = "https://mcp-reports.conversas.ai/mcp"
_RESOURCE_METADATA_URL = "https://mcp-reports.conversas.ai/.well-known/oauth-protected-resource/mcp"
_SCOPE = "mcp:relatorios"
_PROTOCOL_VERSION = "2025-03-26"
_BRAZIL_TZ = ZoneInfo("America/Sao_Paulo")

SYNC_CHECK_INTERVAL_SECONDS = 3 * 60 * 60
_SYNC_MAX_AGE = timedelta(hours=24)
_MOVEMENT_WINDOW_DAYS = 30
_LIVE_CACHE_SECONDS = 120

_auth_metadata: dict | None = None
_live_cache: dict[tuple[UUID, str, int], tuple[float, dict]] = {}


class PactoError(Exception):
    """Falha esperada ao falar com a Pacto (sem acesso, resposta inválida)."""


# --- OAuth -------------------------------------------------------------


async def _auth_server_metadata() -> dict:
    """Endereços de autorização/token/registro, descobertos a partir do
    próprio servidor (não ficam fixos no código)."""
    global _auth_metadata
    if _auth_metadata is None:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resource = (await client.get(_RESOURCE_METADATA_URL)).raise_for_status().json()
            issuer = resource["authorization_servers"][0].rstrip("/")
            metadata = (
                await client.get(f"{issuer}/.well-known/oauth-authorization-server")
            ).raise_for_status().json()
        _auth_metadata = metadata
    return _auth_metadata


async def get_connection(db: AsyncSession, company_id: UUID) -> PactoConnection | None:
    return await db.scalar(select(PactoConnection).where(PactoConnection.company_id == company_id))


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


async def start_authorization(db: AsyncSession, company_id: UUID, redirect_uri: str) -> str:
    """Devolve o endereço da tela de autorização da Pacto. Registra o
    cliente OAuth na primeira vez (ou quando o endereço de retorno muda)."""
    metadata = await _auth_server_metadata()
    connection = await get_connection(db, company_id)
    if connection is None:
        connection = PactoConnection(company_id=company_id)
        db.add(connection)

    if not connection.client_id or connection.redirect_uri != redirect_uri:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                metadata["registration_endpoint"],
                json={
                    "client_name": "Mônica AI",
                    "redirect_uris": [redirect_uri],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "none",
                },
            )
        if resp.status_code >= 400:
            raise PactoError(f"A Pacto recusou o registro da conexão ({resp.status_code}): {resp.text[:300]}")
        registered = resp.json()
        connection.client_id = registered["client_id"]
        secret = registered.get("client_secret")
        connection.client_secret_encrypted = encrypt_secret(secret) if secret else None
        connection.redirect_uri = redirect_uri

    connection.pending_state = secrets.token_urlsafe(32)
    connection.pending_code_verifier = secrets.token_urlsafe(64)
    await db.flush()
    params = {
        "response_type": "code",
        "client_id": connection.client_id,
        "redirect_uri": redirect_uri,
        "state": connection.pending_state,
        "code_challenge": _pkce_challenge(connection.pending_code_verifier),
        "code_challenge_method": "S256",
        "scope": _SCOPE,
        "resource": MCP_URL,
    }
    return f"{metadata['authorization_endpoint']}?{urlencode(params)}"


def _store_tokens(connection: PactoConnection, tokens: dict) -> None:
    connection.access_token_encrypted = encrypt_secret(tokens["access_token"])
    if tokens.get("refresh_token"):
        connection.refresh_token_encrypted = encrypt_secret(tokens["refresh_token"])
    expires_in = int(tokens.get("expires_in") or 3600)
    connection.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    connection.status = "connected"
    connection.last_error = None


async def _token_request(connection: PactoConnection, data: dict) -> dict:
    metadata = await _auth_server_metadata()
    data = {**data, "client_id": connection.client_id}
    if connection.client_secret_encrypted:
        data["client_secret"] = decrypt_secret(connection.client_secret_encrypted)
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(metadata["token_endpoint"], data=data)
    if resp.status_code >= 400:
        raise PactoError(f"A Pacto recusou o acesso ({resp.status_code}): {resp.text[:300]}")
    return resp.json()


async def finish_authorization(db: AsyncSession, company_id: UUID, code: str, state: str) -> PactoConnection:
    """Volta da tela da Pacto: troca o código por tokens e já descobre as
    unidades liberadas."""
    connection = await get_connection(db, company_id)
    if not connection or not connection.pending_state or not secrets.compare_digest(
        connection.pending_state, state or ""
    ):
        raise PactoError("Autorização inválida ou expirada — clique em Conectar de novo.")
    tokens = await _token_request(
        connection,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": connection.redirect_uri,
            "code_verifier": connection.pending_code_verifier,
            "resource": MCP_URL,
        },
    )
    _store_tokens(connection, tokens)
    connection.pending_state = None
    connection.pending_code_verifier = None
    connection.connected_at = datetime.now(timezone.utc)
    await db.flush()
    await discover_units(db, connection)
    return connection


async def _valid_access_token(db: AsyncSession, connection: PactoConnection) -> str:
    if connection.status != "connected" or not connection.access_token_encrypted:
        raise PactoError("Pacto não está conectada.")
    expires_at = connection.token_expires_at
    if expires_at and expires_at - datetime.now(timezone.utc) > timedelta(seconds=60):
        return decrypt_secret(connection.access_token_encrypted)
    if not connection.refresh_token_encrypted:
        connection.status = "error"
        connection.last_error = "O acesso à Pacto expirou — reconecte."
        await db.flush()
        raise PactoError(connection.last_error)
    try:
        tokens = await _token_request(
            connection,
            {
                "grant_type": "refresh_token",
                "refresh_token": decrypt_secret(connection.refresh_token_encrypted),
                "resource": MCP_URL,
            },
        )
    except PactoError as exc:
        connection.status = "error"
        connection.last_error = str(exc)[:500]
        await db.flush()
        raise
    _store_tokens(connection, tokens)
    await db.flush()
    return tokens["access_token"]


async def disconnect(db: AsyncSession, connection: PactoConnection) -> None:
    connection.access_token_encrypted = None
    connection.refresh_token_encrypted = None
    connection.token_expires_at = None
    connection.pending_state = None
    connection.pending_code_verifier = None
    connection.status = "disconnected"
    connection.last_error = None
    await db.flush()


# --- Cliente MCP (HTTP) --------------------------------------------------


def _parse_rpc_response(resp: httpx.Response, request_id: int) -> dict:
    """O servidor pode responder em JSON puro ou em SSE (linhas `data:`)."""
    if "text/event-stream" in resp.headers.get("content-type", ""):
        for line in resp.text.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                message = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict) and message.get("id") == request_id:
                return message
        raise PactoError("Resposta da Pacto sem o resultado esperado.")
    return resp.json()


def _tool_result(message: dict) -> dict:
    if message.get("error"):
        raise PactoError(f"Erro da Pacto: {str(message['error'])[:300]}")
    result = message.get("result") or {}
    if result.get("isError"):
        raise PactoError(f"A Pacto devolveu erro: {str(result.get('content'))[:300]}")
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        inner = structured.get("result")
        return inner if set(structured) == {"result"} and isinstance(inner, dict) else structured
    for item in result.get("content") or []:
        if item.get("type") == "text":
            try:
                parsed = json.loads(item["text"])
            except json.JSONDecodeError as exc:
                raise PactoError("Resposta da Pacto em formato inesperado.") from exc
            if isinstance(parsed, dict):
                return parsed
    raise PactoError("Resposta da Pacto vazia.")


class PactoSession:
    """Uma sessão MCP curta: inicializa e chama ferramentas de relatório."""

    def __init__(self, access_token: str, timeout: float = 30.0):
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        self._client = httpx.AsyncClient(timeout=timeout)
        self._next_id = 0

    async def __aenter__(self) -> "PactoSession":
        try:
            resp = await self._post(
                "initialize",
                {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "monica-ai", "version": "1.0"},
                },
            )
            message = _parse_rpc_response(resp, self._next_id)
            if message.get("error"):
                raise PactoError(f"Erro ao iniciar sessão na Pacto: {str(message['error'])[:300]}")
            session_id = resp.headers.get("mcp-session-id")
            if session_id:
                self._headers["Mcp-Session-Id"] = session_id
            negotiated = (message.get("result") or {}).get("protocolVersion") or _PROTOCOL_VERSION
            self._headers["MCP-Protocol-Version"] = negotiated
            await self._client.post(
                MCP_URL, headers=self._headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"}
            )
        except BaseException:
            await self._client.aclose()
            raise
        return self

    async def __aexit__(self, *_exc) -> None:
        await self._client.aclose()

    async def _post(self, method: str, params: dict) -> httpx.Response:
        self._next_id += 1
        resp = await self._client.post(
            MCP_URL,
            headers=self._headers,
            json={"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params},
        )
        if resp.status_code == 401:
            raise PactoError("A Pacto não reconheceu o acesso (401) — reconecte.")
        if resp.status_code >= 400:
            raise PactoError(f"A Pacto respondeu {resp.status_code}: {resp.text[:300]}")
        return resp

    async def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        resp = await self._post("tools/call", {"name": name, "arguments": arguments or {}})
        return _tool_result(_parse_rpc_response(resp, self._next_id))


# --- Unidades ----------------------------------------------------------

_NAME_NOISE = {"movfit", "acad", "academia", "unid", "unidade", "pa", "ct"}
_ABBREVIATIONS = {"stm": "santarem", "rep": "republica", "n": "novo"}


def _name_tokens(name: str) -> set[str]:
    tokens = {_ABBREVIATIONS.get(t, t) for t in normalize_tokens(name)}
    return {t for t in tokens if t not in _NAME_NOISE and not t.isdigit()}


def match_units(pacto_names: dict[str, str], our_units: dict[str, str]) -> dict[str, str | None]:
    """Sugere qual unidade do catálogo corresponde a cada unidade da Pacto,
    pelo nome ("MOVFIT STM / NOVA REP." → "Santarém - Nova República").
    Recebe {chave: nome} dos dois lados e devolve {chave_pacto: chave_nossa}.
    Resolve primeiro os pares mais parecidos; cada unidade nossa é usada uma
    vez só, então "MOVFIT SANTAREM" fica com a Santarém que sobrar. O
    resultado é só sugestão — o painel deixa corrigir."""
    scored = []
    for pacto_key, pacto_name in pacto_names.items():
        pacto_tokens = _name_tokens(pacto_name)
        for our_key, our_name in our_units.items():
            shared = len(pacto_tokens & _name_tokens(our_name))
            if shared:
                scored.append((shared, pacto_key, our_key))
    result: dict[str, str | None] = {key: None for key in pacto_names}
    used: set[str] = set()
    for _shared, pacto_key, our_key in sorted(scored, key=lambda s: -s[0]):
        if result[pacto_key] is None and our_key not in used:
            result[pacto_key] = our_key
            used.add(our_key)
    return result


async def discover_units(db: AsyncSession, connection: PactoConnection) -> list[PactoUnit]:
    """Lista as unidades liberadas na autorização e cria/atualiza o vínculo
    com o catálogo (sem mexer em vínculo já definido)."""
    token = await _valid_access_token(db, connection)
    found: list[tuple[str, int, str]] = []
    async with PactoSession(token) as session:
        companies = await session.call_tool("listar_empresas_usuario")
        for company in companies.get("empresas") or []:
            chave = company.get("chave_empresa")
            if not chave:
                continue
            units = await session.call_tool("listar_unidades_empresa", {"chave_empresa": chave})
            for unit in units.get("unidades") or []:
                found.append((chave, int(unit["empresa_id"]), unit.get("nome") or company.get("nome_empresa") or chave))

    existing = {
        (u.chave_empresa, u.empresa_id): u
        for u in (
            await db.execute(select(PactoUnit).where(PactoUnit.company_id == connection.company_id))
        ).scalars()
    }
    our_units = (
        await db.execute(
            select(Unit).where(Unit.company_id == connection.company_id, Unit.is_active.is_(True))
        )
    ).scalars().all()
    taken = {str(u.unit_id) for u in existing.values() if u.unit_id}
    suggestions = match_units(
        {f"{chave}:{empresa_id}": name for chave, empresa_id, name in found if (chave, empresa_id) not in existing},
        {str(u.id): u.name for u in our_units if str(u.id) not in taken},
    )
    result = []
    for chave, empresa_id, name in found:
        unit = existing.get((chave, empresa_id))
        if unit is None:
            suggested = suggestions.get(f"{chave}:{empresa_id}")
            unit = PactoUnit(
                company_id=connection.company_id,
                chave_empresa=chave,
                empresa_id=empresa_id,
                pacto_name=name,
                unit_id=UUID(suggested) if suggested else None,
            )
            db.add(unit)
        else:
            unit.pacto_name = name
        result.append(unit)
    await db.flush()
    return result


# --- Movimento -----------------------------------------------------------


def _hour_runs(hours: list[int]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    for hour in sorted(hours):
        if runs and hour == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], hour)
        else:
            runs.append((hour, hour))
    return runs


def _format_hours(hours: list[int]) -> str:
    parts = [
        f"por volta das {start}h" if start == end else f"das {start}h às {(end + 1) % 24}h"
        for start, end in _hour_runs(hours)
    ]
    return " e ".join([", ".join(parts[:-1]), parts[-1]]) if len(parts) > 1 else parts[0]


def summarize_movement(by_hour: dict[int, float], by_weekday: dict[str, float] | None = None) -> str | None:
    """Transforma a % de acessos por hora numa frase que a IA pode repetir.
    Considera só os horários em que a unidade de fato tem movimento (>= 1%
    dos acessos); "cheio" e "tranquilo" são relativos à média deles."""
    open_hours = {hour: pct for hour, pct in by_hour.items() if pct >= 1.0}
    if len(open_hours) < 4:
        return None
    average = sum(open_hours.values()) / len(open_hours)
    busy = [h for h, pct in open_hours.items() if pct >= average * 1.3]
    quiet = [h for h, pct in open_hours.items() if pct <= average * 0.75]
    sentences = []
    if busy:
        sentences.append(f"Mais cheio: {_format_hours(busy)}.")
    if quiet:
        sentences.append(f"Mais tranquilo: {_format_hours(quiet)}.")
    if by_weekday:
        busiest = max(by_weekday, key=by_weekday.get)
        sentence = f"Dia mais movimentado: {busiest.lower()}"
        weekend = sum(pct for day, pct in by_weekday.items() if normalize_text(day).startswith(("sabado", "domingo")))
        if weekend < 12:
            sentence += "; sábado e domingo são bem mais vazios"
        sentences.append(sentence + ".")
    return " ".join(sentences) or None


def _percent(value: object) -> float:
    try:
        return float(str(value).replace("%", "").replace(",", ".").strip())
    except ValueError:
        return 0.0


async def sync_movement(db: AsyncSession, connection: PactoConnection) -> int:
    """Atualiza o resumo de movimento de cada unidade vinculada. Devolve
    quantas foram atualizadas."""
    token = await _valid_access_token(db, connection)
    units = (
        await db.execute(
            select(PactoUnit).where(PactoUnit.company_id == connection.company_id, PactoUnit.unit_id.is_not(None))
        )
    ).scalars().all()
    today = datetime.now(_BRAZIL_TZ).date()
    period = {
        "data_inicio": (today - timedelta(days=_MOVEMENT_WINDOW_DAYS)).isoformat(),
        "data_fim": (today - timedelta(days=1)).isoformat(),
    }
    updated = 0
    async with PactoSession(token) as session:
        for unit in units:
            base = {"chave_empresa": unit.chave_empresa, "empresa_id": unit.empresa_id, **period}
            try:
                hourly = await session.call_tool("totalizador_acessos_empresa", {**base, "frequencia": "GERAL_POR_HORARIO"})
                weekly = await session.call_tool("totalizador_acessos_empresa", {**base, "frequencia": "GERAL_POR_DIA"})
            except PactoError as exc:
                logger.warning("Pacto: falha ao buscar movimento de %s: %s", unit.pacto_name, exc)
                continue
            by_hour = {int(row["hora"]): _percent(row.get("porcentagem")) for row in hourly.get("linhas") or [] if "hora" in row}
            by_weekday = {
                str(row["dia_da_semana"]): _percent(row.get("porcentagem"))
                for row in weekly.get("linhas") or []
                if row.get("dia_da_semana")
            }
            unit.movement = {"by_hour": {str(h): p for h, p in by_hour.items()}, "by_weekday": by_weekday}
            unit.movement_text = summarize_movement(by_hour, by_weekday)
            unit.synced_at = datetime.now(timezone.utc)
            updated += 1
    connection.last_sync_at = datetime.now(timezone.utc)
    await db.flush()
    return updated


async def build_movement_prompt_block(db: AsyncSession, company_id: UUID) -> str:
    """Bloco de sistema com o movimento por unidade — vazio se a Pacto não
    estiver conectada/ligada ou ainda não houver dados."""
    connection = await get_connection(db, company_id)
    if not connection or connection.status != "connected" or not connection.enabled:
        return ""
    rows = (
        await db.execute(
            select(Unit.name, PactoUnit.movement_text)
            .join(PactoUnit, PactoUnit.unit_id == Unit.id)
            .where(PactoUnit.company_id == company_id, PactoUnit.movement_text.is_not(None), Unit.is_active.is_(True))
            .order_by(Unit.name)
        )
    ).all()
    if not rows:
        return ""
    lines = [
        "[Movimento nas unidades — dados automáticos das catracas (Pacto), últimos 30 dias]",
        "Use quando perguntarem sobre lotação, movimento ou melhor horário pra treinar, ou como "
        "argumento ao indicar um horário. Horários são aproximados; não cite percentuais nem "
        "invente dados além destes.",
    ]
    lines.extend(f"- {name}: {text}" for name, text in rows)
    return "\n".join(lines)


_LIVE_WHEN = ("agora", "nesse momento", "neste momento", "nesse horario", "neste horario", "hoje")
_LIVE_WHAT = {
    "cheio", "cheia", "lotado", "lotada", "lotacao", "vazio", "vazia", "movimento", "movimentado",
    "movimentada", "gente", "tranquilo", "tranquila",
}


def asks_live_occupancy(text: str) -> bool:
    """"Tá cheio agora?", "como tá o movimento hoje?"."""
    normalized = normalize_text(text or "")
    return any(when in normalized for when in _LIVE_WHEN) and bool(normalize_tokens(text or "") & _LIVE_WHAT)


async def live_occupancy_note(db: AsyncSession, company_id: UUID, unit: Unit) -> str | None:
    """Frase com quantos alunos estão na unidade neste momento, pra IA
    responder "tá cheio agora?". None se não der pra consultar."""
    connection = await get_connection(db, company_id)
    if not connection or connection.status != "connected" or not connection.enabled:
        return None
    pacto_unit = await db.scalar(
        select(PactoUnit).where(PactoUnit.company_id == company_id, PactoUnit.unit_id == unit.id)
    )
    if not pacto_unit:
        return None
    cache_key = (company_id, pacto_unit.chave_empresa, pacto_unit.empresa_id)
    cached = _live_cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < _LIVE_CACHE_SECONDS:
        data = cached[1]
    else:
        token = await _valid_access_token(db, connection)
        async with PactoSession(token, timeout=8.0) as session:
            data = await session.call_tool(
                "gestao_acessos_empresa",
                {"chave_empresa": pacto_unit.chave_empresa, "empresa_id": pacto_unit.empresa_id},
            )
        _live_cache[cache_key] = (time.monotonic(), data)
    now_count = data.get("alunos_em_tempo_real")
    if now_count is None:
        return None
    moment = str(data.get("momento") or "")[-5:]
    return (
        f"LOTAÇÃO AGORA ({moment}) na unidade {unit.name}, direto da catraca: {now_count} aluno(s) "
        "na academia neste momento. Responda com esse número e, se ajudar, compare com os horários "
        "de pico da unidade."
    )


# --- Rotina em background ------------------------------------------------


async def run_pacto_sync() -> None:
    """Sincroniza as empresas conectadas cujo resumo tem mais de 24h. Lock
    consultivo: só uma cópia do backend faz isso por vez (ver CLAUDE.md,
    "Jobs em background")."""
    async with AsyncSessionLocal() as db:
        async with advisory_lock(db, LOCK_NAMESPACE_PACTO_SYNC) as acquired:
            if not acquired:
                return
            connections = (
                await db.execute(select(PactoConnection).where(PactoConnection.status == "connected"))
            ).scalars().all()
            for connection in connections:
                stale = (
                    connection.last_sync_at is None
                    or datetime.now(timezone.utc) - connection.last_sync_at > _SYNC_MAX_AGE
                )
                if not stale:
                    continue
                try:
                    await discover_units(db, connection)
                    await sync_movement(db, connection)
                    await db.commit()
                except PactoError as exc:
                    # _valid_access_token já marcou a conexão como "error"
                    # quando o problema é de acesso — isso precisa ser salvo.
                    logger.warning("Pacto: sincronização da empresa %s falhou: %s", connection.company_id, exc)
                    await db.commit()
                except Exception:  # noqa: BLE001
                    logger.exception("Pacto: erro inesperado sincronizando a empresa %s", connection.company_id)
                    await db.rollback()


async def periodic_pacto_sync_loop() -> None:
    """Roda pra sempre em background (ver lifespan em app/main.py)."""
    while True:
        try:
            await run_pacto_sync()
        except Exception:  # noqa: BLE001
            logger.exception("Falha na sincronização da Pacto")
        await asyncio.sleep(SYNC_CHECK_INTERVAL_SECONDS)
