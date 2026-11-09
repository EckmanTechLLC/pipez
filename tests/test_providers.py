import json

import httpx2 as httpx
import pytest

from pipez import config
from pipez.llm import decision_schema
from pipez.providers import LLMError, OpenAICompatProvider, ProviderTransientError

TIER = {"provider": "openai_compat", "model": "qwen3", "effort": "low",
        "base_url": "http://vllm.test"}
SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}}


def provider(handler):
    return OpenAICompatProvider(transport=httpx.MockTransport(handler))


def ok(content, finish="stop", model="qwen3-served"):
    return {"model": model, "choices": [{"message": {"content": content},
                                         "finish_reason": finish}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3}}


async def test_request_body_shape():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=ok('{"a": "x"}'))

    out, _ = await provider(handler).complete("SYS", "PROMPT", SCHEMA, TIER)
    assert out == {"a": "x"}
    body = seen["body"]
    assert seen["url"] == "http://vllm.test/v1/chat/completions"
    assert body["model"] == "qwen3"
    assert body["messages"] == [{"role": "system", "content": "SYS"},
                                {"role": "user", "content": "PROMPT"}]
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA
    assert body["chat_template_kwargs"]["enable_thinking"] is False


async def test_think_stripped_and_usage():
    def handler(request):
        return httpx.Response(200, json=ok('<think>hmm {x}</think>\n{"a": "y"}'))

    out, usage = await provider(handler).complete("s", "p", SCHEMA, TIER)
    assert out == {"a": "y"}
    assert usage == {"input_tokens": 7, "output_tokens": 3, "model": "qwen3-served",
                     "structured": "json_schema"}


async def test_length_is_truncated_error():
    def handler(request):
        return httpx.Response(200, json=ok('{"a"', finish="length"))

    with pytest.raises(LLMError, match="truncated"):
        await provider(handler).complete("s", "p", SCHEMA, TIER)


async def test_503_is_transient():
    def handler(request):
        return httpx.Response(503, text="busy")

    with pytest.raises(ProviderTransientError):
        await provider(handler).complete("s", "p", SCHEMA, TIER)


async def test_healthy(monkeypatch):
    monkeypatch.setattr(config, "LOCAL_BASE_URL", "http://vllm.test")
    assert await provider(lambda r: httpx.Response(200, json={})).healthy() is True

    def boom(request):
        raise httpx.ConnectError("refused")

    assert await provider(boom).healthy() is False


def test_decision_schema_local_enum(monkeypatch):
    base = {"deep": {}, "standard": {}, "light": {}}
    monkeypatch.setattr(config, "MODEL_TIERS", dict(base))
    enum = decision_schema()["properties"]["subtasks"]["items"]["properties"]["tier"]["enum"]
    assert "local" not in enum
    monkeypatch.setattr(config, "MODEL_TIERS", dict(base, local={}))
    monkeypatch.setattr(config, "LOCAL_ALLOWED_STAGES", ["decide"])
    enum = decision_schema()["properties"]["subtasks"]["items"]["properties"]["tier"]["enum"]
    assert "local" in enum


# --- AnthropicProvider fallback gating ---

class _Stream:
    def __init__(self, err=None, msg="final"):
        self.err, self.msg = err, msg

    async def __aenter__(self):
        if self.err:
            raise self.err
        return self

    async def __aexit__(self, *a):
        return False

    async def get_final_message(self):
        return self.msg


def _anthropic_provider(beta_err):
    import anthropic
    from types import SimpleNamespace
    from pipez.providers import AnthropicProvider
    p = AnthropicProvider.__new__(AnthropicProvider)
    p._anthropic = anthropic
    p._fallbacks_ok = True
    p._client = SimpleNamespace(
        beta=SimpleNamespace(messages=SimpleNamespace(
            stream=lambda **kw: _Stream(err=beta_err))),
        messages=SimpleNamespace(stream=lambda **kw: _Stream(msg="plain")),
    )
    return p


def _bad_request(message):
    import anthropic
    req = httpx.Request("POST", "http://x/v1/messages")
    resp = httpx.Response(400, request=req)
    return anthropic.BadRequestError(message, response=resp, body=None)


async def test_fallbacks_400_disables_and_retries_plain():
    p = _anthropic_provider(_bad_request("fallbacks not supported"))
    out = await p._create(model="claude-opus-5")
    assert out == "plain"
    assert p._fallbacks_ok is False


async def test_unrelated_400_propagates_and_keeps_fallbacks():
    import anthropic
    p = _anthropic_provider(_bad_request("schema: invalid property"))
    with pytest.raises(anthropic.BadRequestError):
        await p._create(model="claude-opus-5")
    assert p._fallbacks_ok is True
