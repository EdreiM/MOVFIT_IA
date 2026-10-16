"""Provedor Anthropic (Claude): tradução do formato OpenAI, requisição, resposta e custo."""
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import AiConfig
from app.security import decrypt_secret
from app.services import llm
from app.services.llm import (
    _anthropic_to_openai_message,
    _anthropic_usage,
    _build_anthropic_request,
    _to_anthropic_tools,
    to_anthropic_messages,
)
from app.services.llm_usage import collect_llm_usage, cost_usd


def text(content):
    return {"type": "text", "text": content}


def test_leading_system_messages_become_the_system_prompt_and_roles_alternate():
    system, msgs = to_anthropic_messages(
        [
            {"role": "system", "content": "Você é a Mônica."},
            {"role": "system", "content": "Fale como no WhatsApp."},
            {"role": "user", "content": "oi"},
            {"role": "user", "content": "tem plano?"},
        ]
    )
    assert system == ["Você é a Mônica.", "Fale como no WhatsApp."]
    assert msgs == [{"role": "user", "content": [text("oi"), text("tem plano?")]}]


def test_turn_reminders_after_the_customer_message_keep_their_position():
    _, msgs = to_anthropic_messages(
        [
            {"role": "system", "content": "base"},
            {"role": "user", "content": "quero o preço"},
            {"role": "system", "content": "LEMBRETE 1"},
            {"role": "system", "content": "LEMBRETE 2"},
        ]
    )
    assert msgs[-1] == {"role": "system", "content": "LEMBRETE 1\n\nLEMBRETE 2"}
    assert msgs[0]["role"] == "user"


def test_a_system_message_in_a_place_the_api_refuses_goes_to_the_top_system():
    system, msgs = to_anthropic_messages(
        [
            {"role": "system", "content": "base"},
            {"role": "user", "content": "oi"},
            {"role": "assistant", "content": "olá!"},
            {"role": "system", "content": "depois de uma resposta da IA"},
            {"role": "user", "content": "e aí"},
        ]
    )
    assert system == ["base", "depois de uma resposta da IA"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]


def test_tool_round_trip_uses_tool_use_and_one_user_message_of_tool_results():
    calls = [
        {"id": "c1", "type": "function", "function": {"name": "consulta", "arguments": '{"cpf": "123"}'}},
        {"id": "c2", "type": "function", "function": {"name": "horarios", "arguments": "{}"}},
    ]
    _, msgs = to_anthropic_messages(
        [
            {"role": "user", "content": "meu cpf 123"},
            {"role": "assistant", "content": "Vou consultar", "tool_calls": calls},
            {"role": "tool", "tool_call_id": "c1", "content": '{"sucesso": true}'},
            {"role": "tool", "tool_call_id": "c2", "content": ""},
        ]
    )
    assert msgs[1]["content"] == [
        text("Vou consultar"),
        {"type": "tool_use", "id": "c1", "name": "consulta", "input": {"cpf": "123"}},
        {"type": "tool_use", "id": "c2", "name": "horarios", "input": {}},
    ]
    assert msgs[2] == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "c1", "content": '{"sucesso": true}'},
            {"type": "tool_result", "tool_use_id": "c2", "content": "(sem conteúdo)"},
        ],
    }


def test_stashed_claude_blocks_are_replayed_exactly_with_their_thinking():
    raw = [
        {"type": "thinking", "thinking": "", "signature": "sig"},
        {"type": "tool_use", "id": "c1", "name": "consulta", "input": {"a": 1}},
    ]
    call = {"id": "c1", "type": "function", "function": {"name": "consulta", "arguments": '{"a": 1}'}, "_anthropic_blocks": raw}
    _, msgs = to_anthropic_messages(
        [
            {"role": "user", "content": "oi"},
            {"role": "assistant", "content": None, "tool_calls": [call]},
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        ]
    )
    assert msgs[1]["content"] == raw


def test_conversation_ending_with_the_ai_gets_a_user_turn_and_starting_with_the_ai_too():
    # follow-up: o histórico termina numa fala da própria IA
    _, msgs = to_anthropic_messages(
        [
            {"role": "system", "content": "reengaje"},
            {"role": "assistant", "content": "Posso ajudar?"},
        ]
    )
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert "ainda não respondeu" in msgs[-1]["content"][0]["text"]


def test_images_become_image_blocks():
    _, msgs = to_anthropic_messages(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "descreva"},
                    {"type": "image_url", "image_url": {"url": "https://x.test/a.jpg"}},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                ],
            }
        ]
    )
    assert msgs[0]["content"][1] == {"type": "image", "source": {"type": "url", "url": "https://x.test/a.jpg"}}
    assert msgs[0]["content"][2] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"},
    }


def test_openai_tool_schema_becomes_input_schema():
    tools = _to_anthropic_tools(
        [
            {"type": "function", "function": {"name": "a", "description": "faz a", "parameters": {"type": "object", "properties": {"x": {"type": "string"}}}}},
            {"type": "function", "function": {"name": "b", "parameters": None}},
        ]
    )
    assert tools[0] == {
        "name": "a",
        "description": "faz a",
        "input_schema": {"type": "object", "properties": {"x": {"type": "string"}}},
    }
    assert tools[1]["input_schema"] == {"type": "object", "properties": {}}


def test_request_has_cache_on_the_base_prompt_effort_and_never_sends_temperature():
    system, msgs = to_anthropic_messages([{"role": "system", "content": "base"}, {"role": "user", "content": "oi"}])
    req = _build_anthropic_request("claude-haiku-5-5", system, msgs, [], simple=False)
    assert req["system"] == [{"type": "text", "text": "base", "cache_control": {"type": "ephemeral"}}]
    assert req["output_config"] == {"effort": "low"}
    assert "temperature" not in req and "thinking" not in req and "tools" not in req
    assert req["model"] == "claude-haiku-5-5"


def test_thinking_is_turned_off_only_when_a_tool_turn_lost_its_thinking_blocks():
    calls = [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]
    convo = [
        {"role": "user", "content": "oi"},
        {"role": "assistant", "content": None, "tool_calls": calls},
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ]
    system, msgs = to_anthropic_messages(convo)
    assert _build_anthropic_request("m", system, msgs, None, simple=False)["thinking"] == {"type": "disabled"}

    calls[0]["_anthropic_blocks"] = [
        {"type": "thinking", "thinking": "", "signature": "s"},
        {"type": "tool_use", "id": "c1", "name": "f", "input": {}},
    ]
    system, msgs = to_anthropic_messages(convo)
    assert "thinking" not in _build_anthropic_request("m", system, msgs, None, simple=False)


def test_simple_mode_folds_system_entries_and_strips_thinking():
    calls = [
        {
            "id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"},
            "_anthropic_blocks": [{"type": "thinking", "thinking": "", "signature": "s"}, {"type": "tool_use", "id": "c1", "name": "f", "input": {}}],
        }
    ]
    system, msgs = to_anthropic_messages(
        [
            {"role": "system", "content": "base"},
            {"role": "user", "content": "oi"},
            {"role": "system", "content": "LEMBRETE"},
            {"role": "assistant", "content": None, "tool_calls": calls},
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        ]
    )
    assert any(m["role"] == "system" for m in msgs)
    req = _build_anthropic_request("m", system, msgs, None, simple=True)
    assert all(m["role"] != "system" for m in req["messages"])
    assert [b["text"] for b in req["system"]] == ["base", "LEMBRETE"]
    assert not any(b["type"] == "thinking" for m in req["messages"] for b in m["content"])
    assert req["thinking"] == {"type": "disabled"}


class _Block(SimpleNamespace):
    def model_dump(self, **_):
        return {k: v for k, v in vars(self).items() if v is not None}


def _response(content, stop_reason="end_turn", usage=None, model="claude-haiku-5-5"):
    usage = usage or SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return SimpleNamespace(content=content, stop_reason=stop_reason, usage=usage, model=model)


def test_response_with_tool_calls_comes_back_in_openai_shape_and_keeps_the_raw_blocks():
    message = _anthropic_to_openai_message(
        _response(
            [
                _Block(type="thinking", thinking="", signature="sig"),
                _Block(type="text", text="Já vejo isso"),
                _Block(type="tool_use", id="toolu_1", name="consulta", input={"nome": "João"}),
            ]
        )
    )
    assert message["content"] == "Já vejo isso"
    call = message["tool_calls"][0]
    assert call["id"] == "toolu_1" and call["function"]["name"] == "consulta"
    assert json.loads(call["function"]["arguments"]) == {"nome": "João"}
    assert [b["type"] for b in call["_anthropic_blocks"]] == ["thinking", "text", "tool_use"]


def test_plain_text_response_has_no_tool_calls():
    message = _anthropic_to_openai_message(_response([_Block(type="text", text="Olá!")]))
    assert message == {"role": "assistant", "content": "Olá!"}


def test_usage_and_cost_with_cache_reads_and_writes():
    usage = SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=4000, cache_creation_input_tokens=800)
    mapped = _anthropic_usage(usage)
    # escrita em cache vale 1,25x a entrada: 800 -> 1000 tokens equivalentes
    assert mapped == {"prompt_tokens": 1000 + 4000 + 1000, "prompt_tokens_details": {"cached_tokens": 4000}, "completion_tokens": 200}
    cost = cost_usd("claude-haiku-5-5", mapped["prompt_tokens"], 4000, 200)
    assert cost == (Decimal(2000) * Decimal("0.10") + 4000 * Decimal("0.01") + 200 * Decimal("0.50")) / 1_000_000


class _FakeMessages:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _fake_client(monkeypatch, outcomes):
    import anthropic

    messages = _FakeMessages(outcomes)

    class _Client:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.messages = messages

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

    monkeypatch.setattr(anthropic, "AsyncAnthropic", _Client)
    return messages


@pytest.mark.asyncio
async def test_chat_completion_routes_to_anthropic_and_records_usage(monkeypatch):
    fake = _fake_client(monkeypatch, [_response([_Block(type="text", text="Oi, tudo bem?")])])
    with collect_llm_usage() as bucket:
        message = await llm.chat_completion(
            provider="anthropic",
            model="claude-haiku-5-5",
            api_key="sk-ant-test",
            messages=[{"role": "system", "content": "base"}, {"role": "user", "content": "oi"}],
            temperature=0.3,
        )
    assert message["content"] == "Oi, tudo bem?"
    assert "temperature" not in fake.requests[0]
    assert bucket == [{"model": "claude-haiku-5-5", "prompt_tokens": 100, "cached_tokens": 0, "completion_tokens": 20}]


@pytest.mark.asyncio
async def test_a_400_is_retried_once_in_simple_format(monkeypatch):
    import anthropic
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    bad = anthropic.BadRequestError(
        "role 'system' is not supported", response=httpx.Response(400, request=request), body=None
    )
    fake = _fake_client(monkeypatch, [bad, _response([_Block(type="text", text="ok")])])
    message = await llm.chat_completion(
        provider="anthropic",
        model="claude-haiku-5-5",
        api_key="k",
        messages=[
            {"role": "system", "content": "base"},
            {"role": "user", "content": "oi"},
            {"role": "system", "content": "LEMBRETE"},
        ],
    )
    assert message["content"] == "ok"
    assert len(fake.requests) == 2
    assert any(m["role"] == "system" for m in fake.requests[0]["messages"])
    assert all(m["role"] != "system" for m in fake.requests[1]["messages"])


@pytest.mark.asyncio
async def test_a_refusal_raises_so_the_callers_fallback_answer_is_used(monkeypatch):
    _fake_client(monkeypatch, [_response([], stop_reason="refusal")])
    with pytest.raises(RuntimeError):
        await llm.chat_completion(
            provider="anthropic", model="claude-haiku-5-5", api_key="k", messages=[{"role": "user", "content": "oi"}]
        )


@pytest.mark.asyncio
async def test_unknown_provider_is_still_refused():
    with pytest.raises(ValueError):
        await llm.chat_completion(provider="outro", model="x", api_key="k", messages=[])


# --- Tela Config. IA: provedor, modelo e chave precisam combinar ----------------


@pytest.mark.asyncio
async def test_switching_provider_needs_the_new_key_and_a_matching_model(client, db_session, company, user):
    login = await client.post("/auth/login", json={"email": "teste@movfit.com", "password": "senha-teste-123"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    url = f"/ai-configs/{company.id}"

    no_key = await client.patch(url, json={"llm_provider": "anthropic", "llm_model": "claude-haiku-5-5"}, headers=headers)
    assert no_key.status_code == 400 and "API Key" in no_key.json()["detail"]

    wrong_model = await client.patch(
        url, json={"llm_provider": "anthropic", "llm_model": "gpt-4o", "llm_api_key": "sk-ant-x"}, headers=headers
    )
    assert wrong_model.status_code == 400

    openai_with_claude = await client.patch(url, json={"llm_model": "claude-haiku-5-5"}, headers=headers)
    assert openai_with_claude.status_code == 400

    ok = await client.patch(
        url,
        json={"llm_provider": "anthropic", "llm_model": "claude-haiku-5-5", "llm_api_key": "sk-ant-secreta"},
        headers=headers,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["llm_provider"] == "anthropic" and ok.json()["llm_model"] == "claude-haiku-5-5"
    config = (await db_session.execute(select(AiConfig).where(AiConfig.company_id == company.id))).scalars().first()
    assert decrypt_secret(config.llm_api_key_encrypted) == "sk-ant-secreta"

    # voltar pro provedor antigo também exige a chave dele
    back = await client.patch(url, json={"llm_provider": "openai", "llm_model": "gpt-4.1-mini"}, headers=headers)
    assert back.status_code == 400
