from __future__ import annotations

import json
import logging
import math
import re
from typing import Any

import httpx

from app.config import get_settings
from app.services.llm_usage import note_llm_usage

logger = logging.getLogger(__name__)


async def chat_completion(
    *,
    provider: str,
    model: str,
    api_key: str,
    messages: list[dict[str, Any]],
    temperature: float = 0.3,
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Retorna a mensagem completa do assistente (dict com `content` e,
    quando o modelo decide chamar uma ferramenta, `tool_calls`)."""
    provider = (provider or "openai").lower()
    if provider == "openai":
        return await _openai_chat(
            model=model, api_key=api_key, messages=messages, temperature=temperature, tools=tools
        )
    if provider == "anthropic":
        # `temperature` não é enviada: os modelos Claude atuais (Haiku 5.5 etc.)
        # recusam qualquer valor diferente do padrão.
        return await _anthropic_chat(model=model, api_key=api_key, messages=messages, tools=tools)
    # Extensível: azure_openai, etc.
    raise ValueError(f"Provedor LLM não suportado ainda: {provider}")


async def _openai_chat(
    *,
    model: str,
    api_key: str,
    messages: list[dict[str, Any]],
    temperature: float,
    tools: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        body["tools"] = tools
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, headers=headers, json=body)
        if resp.status_code >= 400:
            logger.error("OpenAI error %s: %s", resp.status_code, resp.text)
            resp.raise_for_status()
        data = resp.json()
        note_llm_usage(data.get("model") or model, data.get("usage"))
        return data["choices"][0]["message"]


# --- Anthropic (Claude) ------------------------------------------------------
#
# O resto do sistema fala o formato da OpenAI (mensagens system/user/assistant/
# tool, ferramentas como "function"). Aqui isso é traduzido pro formato da
# Messages API da Anthropic e a resposta volta no formato da OpenAI — assim
# nenhum chamador precisa saber qual provedor está por trás.

_ANTHROPIC_MAX_TOKENS = 8000  # o raciocínio do modelo também conta nesse teto
_START_TURN = "(início da conversa)"
_CONTINUE_TURN = "(O cliente ainda não respondeu. Escreva a próxima mensagem conforme as instruções do sistema.)"
_DATA_URL = re.compile(r"^data:(?P<mime>[\w/+.-]+);base64,(?P<data>.+)$", re.DOTALL)
_STASH_KEY = "_anthropic_blocks"


def _plain_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict) and part.get("type") == "text":
            parts.append(part.get("text") or "")
    return "\n".join(p for p in parts if p)


def _image_block(url: str | None) -> dict | None:
    if not url:
        return None
    match = _DATA_URL.match(url)
    if match:
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": match["mime"], "data": match["data"]},
        }
    return {"type": "image", "source": {"type": "url", "url": url}}


def _user_blocks(content: Any) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content.strip() else []
    blocks: list[dict] = []
    for part in content or []:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            if (part.get("text") or "").strip():
                blocks.append({"type": "text", "text": part["text"]})
        elif part.get("type") == "image_url":
            image = part.get("image_url")
            block = _image_block(image.get("url") if isinstance(image, dict) else image)
            if block:
                blocks.append(block)
    return blocks


def _append(out: list[dict], role: str, blocks: list[dict]) -> None:
    """Mensagens seguidas do mesmo papel viram uma só (a API pede alternância)."""
    if out and out[-1]["role"] == role and isinstance(out[-1]["content"], list):
        out[-1]["content"].extend(blocks)
    else:
        out.append({"role": role, "content": list(blocks)})


def to_anthropic_messages(messages: list[dict[str, Any]]) -> tuple[list[str], list[dict]]:
    """Mensagens no formato OpenAI → (textos do system, mensagens da Anthropic).

    - As mensagens "system" do começo viram o `system` da requisição.
    - Instruções "system" que vêm DEPOIS do histórico (os "LEMBRETE DESTE TURNO",
      que o fluxo põe de propósito perto da pergunta) ficam na mesma posição, como
      mensagem de sistema no meio da conversa — quando a API aceita essa posição
      (logo depois de uma mensagem do cliente e antes de uma resposta ou do fim);
      senão entram no `system` do topo.
    - tool_calls viram blocos tool_use; resultados "tool" viram tool_result.
    """
    system_texts: list[str] = []
    out: list[dict] = []
    total = len(messages)
    i = 0
    while i < total and messages[i].get("role") == "system":
        system_texts.append(_plain_text(messages[i].get("content")))
        i += 1

    while i < total:
        msg = messages[i]
        role = msg.get("role")
        if role == "system":
            j = i
            texts = []
            while j < total and messages[j].get("role") == "system":
                texts.append(_plain_text(messages[j].get("content")))
                j += 1
            text = "\n\n".join(t for t in texts if t.strip())
            if text:
                next_role = messages[j].get("role") if j < total else None
                if out and out[-1]["role"] == "user" and next_role in (None, "assistant"):
                    out.append({"role": "system", "content": text})
                else:
                    system_texts.append(text)
            i = j
            continue
        if role == "user":
            blocks = _user_blocks(msg.get("content"))
            if blocks:
                _append(out, "user", blocks)
        elif role == "assistant":
            tool_calls = msg.get("tool_calls") or []
            stash = next((c[_STASH_KEY] for c in tool_calls if isinstance(c, dict) and c.get(_STASH_KEY)), None)
            if stash:
                # Resposta anterior do próprio Claude: volta exatamente como veio
                # (com o raciocínio), que é o que a API espera junto dos resultados.
                blocks = list(stash)
            else:
                blocks = []
                text = _plain_text(msg.get("content"))
                if text.strip():
                    blocks.append({"type": "text", "text": text})
                for call in tool_calls:
                    fn = call.get("function") or {}
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": call.get("id"),
                            "name": fn.get("name"),
                            "input": args if isinstance(args, dict) else {},
                        }
                    )
            if blocks:
                _append(out, "assistant", blocks)
        elif role == "tool":
            _append(
                out,
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": msg.get("tool_call_id"),
                        "content": _plain_text(msg.get("content")) or "(sem conteúdo)",
                    }
                ],
            )
        i += 1

    if not out or out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": [{"type": "text", "text": _START_TURN}]})
    if out[-1]["role"] == "assistant":
        # Terminar numa fala do assistente é "prefill", que os modelos novos recusam
        # (acontece no follow-up: a última mensagem da conversa foi da própria IA).
        out.append({"role": "user", "content": [{"type": "text", "text": _CONTINUE_TURN}]})
    return system_texts, out


def _to_anthropic_tools(tools: list[dict[str, Any]] | None) -> list[dict]:
    converted = []
    for tool in tools or []:
        fn = tool.get("function") if tool.get("type") == "function" else tool
        if not isinstance(fn, dict) or not fn.get("name"):
            continue
        schema = fn.get("parameters")
        schema = dict(schema) if isinstance(schema, dict) else {}
        if schema.get("type") not in (None, "object"):
            schema = {}
        schema["type"] = "object"
        schema.setdefault("properties", {})
        converted.append({"name": fn["name"], "description": fn.get("description") or fn["name"], "input_schema": schema})
    return converted


def _is_thinking_block(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") in ("thinking", "redacted_thinking")


def _build_anthropic_request(
    model: str, system_texts: list[str], msgs: list[dict], tools: list[dict[str, Any]] | None, *, simple: bool
) -> dict[str, Any]:
    """`simple` = rede de segurança (reenvio depois de um erro 400): tudo que é
    instrução vai pro `system` do topo e o raciocínio do modelo sai da conversa."""
    system_texts = list(system_texts)
    msgs = [dict(m) for m in msgs]
    if simple:
        folded = [m for m in msgs if m["role"] == "system"]
        msgs = [m for m in msgs if m["role"] != "system"]
        system_texts += [m["content"] for m in folded]
    assistants = [m for m in msgs if m["role"] == "assistant"]
    # A API exige os blocos de raciocínio de volta junto dos resultados de
    # ferramenta; se algum turno com ferramenta chegou sem eles, desliga o
    # raciocínio nessa chamada (em vez de falhar).
    tool_turn_without_thinking = any(
        any(b.get("type") == "tool_use" for b in m["content"]) and not any(_is_thinking_block(b) for b in m["content"])
        for m in assistants
    )
    disable_thinking = simple or tool_turn_without_thinking
    if disable_thinking:
        for m in assistants:
            m["content"] = [b for b in m["content"] if not _is_thinking_block(b)]

    blocks = [{"type": "text", "text": t} for t in system_texts if t.strip()]
    if blocks:
        # O primeiro bloco (prompt base da empresa) é igual entre mensagens: marca
        # pra cache — o que vem antes dele (as ferramentas) entra junto.
        blocks[0]["cache_control"] = {"type": "ephemeral"}
    request: dict[str, Any] = {"model": model, "max_tokens": _ANTHROPIC_MAX_TOKENS, "messages": msgs}
    if blocks:
        request["system"] = blocks
    anthropic_tools = _to_anthropic_tools(tools)
    if anthropic_tools:
        request["tools"] = anthropic_tools
    effort = (get_settings().anthropic_effort or "").strip()
    if effort:
        request["output_config"] = {"effort": effort}
    if disable_thinking:
        request["thinking"] = {"type": "disabled"}
    return request


def _anthropic_usage(usage: Any) -> dict[str, Any]:
    """Uso da Anthropic → formato que o painel de custo já entende (o da OpenAI).
    Escrever no cache custa 1,25x a entrada: esses tokens entram na conta já
    multiplicados, pro custo sair certo (por isso o total de tokens fica um
    pouco acima do real quando há escrita em cache)."""
    fresh = int(getattr(usage, "input_tokens", 0) or 0)
    cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    return {
        "prompt_tokens": fresh + cache_read + math.ceil(cache_write * 1.25),
        "prompt_tokens_details": {"cached_tokens": cache_read},
        "completion_tokens": int(getattr(usage, "output_tokens", 0) or 0),
    }


def _anthropic_to_openai_message(resp: Any) -> dict[str, Any]:
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    tool_calls = [
        {
            "id": b.id,
            "type": "function",
            "function": {"name": b.name, "arguments": json.dumps(b.input, ensure_ascii=False)},
        }
        for b in resp.content
        if b.type == "tool_use"
    ]
    message: dict[str, Any] = {"role": "assistant", "content": text or None}
    if tool_calls:
        # Guarda a resposta crua (com o raciocínio) na primeira chamada: é
        # devolvida como veio quando os resultados das ferramentas voltarem.
        tool_calls[0][_STASH_KEY] = [b.model_dump(mode="json", exclude_none=True) for b in resp.content]
        message["tool_calls"] = tool_calls
    return message


async def _anthropic_chat(
    *,
    model: str,
    api_key: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    import anthropic

    system_texts, msgs = to_anthropic_messages(messages)
    async with anthropic.AsyncAnthropic(api_key=api_key, timeout=60.0, max_retries=2) as client:
        try:
            resp = await client.messages.create(**_build_anthropic_request(model, system_texts, msgs, tools, simple=False))
        except anthropic.BadRequestError as exc:
            logger.warning("Anthropic recusou a requisição (%s) — reenviando em formato simples.", exc)
            resp = await client.messages.create(**_build_anthropic_request(model, system_texts, msgs, tools, simple=True))
    note_llm_usage(getattr(resp, "model", None) or model, _anthropic_usage(resp.usage))
    if resp.stop_reason == "refusal":
        logger.error("Anthropic recusou responder (%s).", getattr(resp, "stop_details", None))
        raise RuntimeError("A Anthropic recusou responder a essa mensagem.")
    if resp.stop_reason == "max_tokens":
        logger.warning("Resposta da Anthropic cortada por max_tokens (%s).", _ANTHROPIC_MAX_TOKENS)
    return _anthropic_to_openai_message(resp)
