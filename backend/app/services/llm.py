from __future__ import annotations

import logging
from typing import Any

import httpx

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
    # Extensível: anthropic, azure_openai, etc.
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
        return data["choices"][0]["message"]
