import importlib
import json

import httpx2 as httpx
import pytest

from pipez import config
from pipez.llm import LLM
from pipez.providers import LLMError, OpenAICompatProvider, ProviderSchemaError

VARS = ["PIPEZ_LOCAL_BASE_URL", "PIPEZ_LOCAL_MODEL", "PIPEZ_LOCAL_REASON_BASE_URL",
        "PIPEZ_LOCAL_REASON_MODEL", "PIPEZ_LOCAL_STRUCTURED",
        "PIPEZ_LOCAL_REASON_STRUCTURED"]

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["a", "items"],
          "properties": {"a": {"type": "string"},
                         "items": {"type": "array", "items": {
                             "type": "object", "required": ["kind"],
                             "properties": {"kind": {"enum": ["x", "y"]}}}}}}
GOOD = {"a": "hi", "items": [{"kind": "x"}]}


@pytest.fixture
def cfg(monkeypatch):
    def load(**env):
        for v in VARS:
            monkeypatch.setenv(v, "")
        monkeypatch.setenv("PIPEZ_LOCAL_BASE_URL", "http://c.test")
        monkeypatch.setenv("PIPEZ_LOCAL_MODEL", "coder")
        monkeypatch.setenv("PIPEZ_LOCAL_REASON_BASE_URL", "http://r.test")
        monkeypatch.setenv("PIPEZ_LOCAL_REASON_MODEL", "reasoner")
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        return importlib.reload(config)
    yield load
    monkeypatch.undo()
    importlib.reload(config)


def test_config_structured(cfg):
    c = cfg()
    assert c.MODEL_TIERS["local"]["structured"] == "json_schema"
    assert c.MODEL_TIERS["local_reason"]["structured"] == "json_object"
    assert "structured" not in c.MODEL_TIERS["deep"]
    c = cfg(PIPEZ_LOCAL_STRUCTURED="prompt", PIPEZ_LOCAL_REASON_STRUCTURED="prompt")
    assert c.MODEL_TIERS["local"]["structured"] == "prompt"
    assert c.MODEL_TIERS["local_reason"]["structured"] == "prompt"
    c = cfg(PIPEZ_LOCAL_STRUCTURED="bogus", PIPEZ_LOCAL_REASON_STRUCTURED="nope")
    assert c.MODEL_TIERS["local"]["structured"] == "json_schema"
    assert c.MODEL_TIERS["local_reason"]["structured"] == "json_object"


def _tier(mode):
    t = {"provider": "openai_compat", "model": "m", "effort": "low",
         "base_url": "http://c.test", "api_key": "", "timeout": 7.0,
         "temperature": 0.2, "thinking": False, "reasoning_effort": None,
         "max_tokens": 100}
    if mode:
        t["structured"] = mode
    return t


def _provider(content, seen=None):
    def handler(request):
        if seen is not None:
            seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": content},
                                                      "finish_reason": "stop"}]})
    return OpenAICompatProvider(transport=httpx.MockTransport(handler))


async def test_body_per_mode():
    seen = {}
    p = _provider(json.dumps(GOOD), seen)
    _, usage = await p.complete("s", "PROMPT", SCHEMA, _tier("json_schema"))
    b = seen["body"]
    assert b["response_format"]["type"] == "json_schema"
    assert b["response_format"]["json_schema"]["strict"] is True
    assert b["messages"][1]["content"] == "PROMPT"
    assert usage["structured"] == "json_schema"

    _, usage = await p.complete("s", "PROMPT", SCHEMA, _tier("json_object"))
    b = seen["body"]
    assert b["response_format"] == {"type": "json_object"}
    assert b["messages"][1]["content"].startswith("PROMPT\n\n## Output format\n")
    assert b["messages"][1]["content"].endswith(json.dumps(SCHEMA))
    assert usage["structured"] == "json_object"

    _, usage = await p.complete("s", "PROMPT", SCHEMA, _tier("prompt"))
    b = seen["body"]
    assert "response_format" not in b
    assert b["messages"][1]["content"].endswith(json.dumps(SCHEMA))
    assert usage["structured"] == "prompt"


async def test_parse_fence_and_prose_in_prompt_mode():
    for content in ("```json\n%s\n```" % json.dumps(GOOD),
                    "Here is the plan: %s\nHope that helps" % json.dumps(GOOD)):
        out, _ = await _provider(content).complete("s", "p", SCHEMA, _tier("prompt"))
        assert out == GOOD


async def test_json_schema_mode_invalid_json_still_errors():
    with pytest.raises(LLMError, match="invalid JSON") as ei:
        await _provider("not json").complete("s", "p", SCHEMA, _tier("json_schema"))
    assert not isinstance(ei.value, ProviderSchemaError)


@pytest.mark.parametrize("bad", [{"a": "hi"},
                                 {"a": "hi", "items": [{"kind": "z"}]}])
@pytest.mark.parametrize("mode", ["json_schema", "json_object", "prompt"])
async def test_schema_violation(bad, mode, monkeypatch):
    monkeypatch.setattr(config, "RETRY_BASE_DELAY", 0)
    with pytest.raises(ProviderSchemaError) as ei:
        await _provider(json.dumps(bad)).complete("s", "p", SCHEMA, _tier(mode))
    assert ei.value.retryable is False
    assert mode in str(ei.value) and "m" in str(ei.value)

    calls = []
    inner = _provider(json.dumps(bad))

    class Counting(OpenAICompatProvider):
        async def complete(self, *a, **k):
            calls.append(1)
            return await inner.complete(*a, **k)
    llm = LLM(providers={"openai_compat": Counting()})
    with pytest.raises(ProviderSchemaError):
        await llm._call("s", "p", SCHEMA, tier=_tier(mode))
    assert len(calls) == 1
