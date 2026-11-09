import importlib
import json
import logging

import httpx2 as httpx
import pytest

from pipez import config, seed_prompts
from pipez.db import Database
from pipez.engine import Engine
from pipez.events import EventBus
from pipez.llm import LLM
from pipez.providers import (LLMError, OpenAICompatProvider, Provider,
                             ProviderTimeoutError, ProviderTransientError,
                             ProviderTruncatedError, is_transient)

VARS = ["PIPEZ_LOCAL_BASE_URL", "PIPEZ_LOCAL_MODEL", "PIPEZ_LOCAL_REASON_BASE_URL",
        "PIPEZ_LOCAL_REASON_MODEL", "PIPEZ_LOCAL_MAX_TOKENS",
        "PIPEZ_LOCAL_REASON_MAX_TOKENS", "PIPEZ_LOCAL_STAGES",
        "PIPEZ_LOCAL_REASON_STAGES", "PIPEZ_LOCAL_REASON_TIMEOUT",
        "PIPEZ_LOCAL_REASON_TEMPERATURE", "PIPEZ_LOCAL_REASON_API_KEY"]


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


def test_config_max_tokens(cfg):
    c = cfg()
    assert c.MODEL_TIERS["local"]["max_tokens"] == 16000
    assert c.MODEL_TIERS["local_reason"]["max_tokens"] == 16000
    c = cfg(PIPEZ_LOCAL_MAX_TOKENS="1234")
    assert c.MODEL_TIERS["local"]["max_tokens"] == 1234
    assert c.MODEL_TIERS["local_reason"]["max_tokens"] == 1234
    c = cfg(PIPEZ_LOCAL_MAX_TOKENS="1234", PIPEZ_LOCAL_REASON_MAX_TOKENS="777")
    assert c.MODEL_TIERS["local"]["max_tokens"] == 1234
    assert c.MODEL_TIERS["local_reason"]["max_tokens"] == 777
    assert "max_tokens" not in c.MODEL_TIERS["deep"]


def _tier(name="coder", max_tokens=321):
    return {"provider": "openai_compat", "model": name, "effort": "low",
            "base_url": "http://c.test", "api_key": "", "timeout": 7.0,
            "temperature": 0.2, "thinking": False, "reasoning_effort": None,
            "max_tokens": max_tokens}


async def test_provider_sends_tier_max_tokens():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"},
                                                      "finish_reason": "stop"}]})
    p = OpenAICompatProvider(transport=httpx.MockTransport(handler))
    await p.complete("s", "p", {"type": "object"}, _tier(max_tokens=321))
    assert seen["body"]["max_tokens"] == 321


async def test_provider_timeout_error():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)
    p = OpenAICompatProvider(transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderTimeoutError) as ei:
        await p.complete("s", "p", {"type": "object"}, _tier())
    assert not is_transient(ei.value)
    assert ei.value.retryable is False
    assert "coder" in str(ei.value) and "http://c.test" in str(ei.value) \
        and "7.0" in str(ei.value)


async def test_provider_other_transport_error_stays_transient():
    def handler(request):
        raise httpx.ConnectError("nope", request=request)
    p = OpenAICompatProvider(transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderTransientError):
        await p.complete("s", "p", {"type": "object"}, _tier())


async def test_provider_truncated_error():
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "{"},
                                                      "finish_reason": "length"}],
                                         "usage": {"completion_tokens": 321}})
    p = OpenAICompatProvider(transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderTruncatedError) as ei:
        await p.complete("s", "p", {"type": "object"}, _tier())
    assert "truncated" in str(ei.value) and "max_tokens=321" in str(ei.value)
    assert ei.value.retryable is False


class RaisingProvider(Provider):
    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    async def complete(self, system, prompt, schema, tier, *, fetch=False):
        self.calls += 1
        raise self.exc

    async def healthy(self, tier=None):
        return True


async def test_facade_no_retry_on_timeout(monkeypatch, caplog):
    monkeypatch.setattr(config, "RETRY_BASE_DELAY", 0)
    stub = RaisingProvider(ProviderTimeoutError("timed out"))
    llm = LLM(providers={"openai_compat": stub})
    with caplog.at_level(logging.WARNING, logger="pipez.llm"):
        with pytest.raises(ProviderTimeoutError):
            await llm._call("s", "p", {}, tier=_tier())
    assert stub.calls == 1
    assert any("not retrying: timed out" in r.getMessage() for r in caplog.records)


async def test_facade_no_retry_on_truncated(monkeypatch):
    monkeypatch.setattr(config, "RETRY_BASE_DELAY", 0)
    stub = RaisingProvider(ProviderTruncatedError("response truncated at max_tokens=1"))
    with pytest.raises(ProviderTruncatedError):
        await LLM(providers={"openai_compat": stub})._call("s", "p", {}, tier=_tier())
    assert stub.calls == 1


async def test_facade_still_retries_transient(monkeypatch):
    monkeypatch.setattr(config, "RETRY_BASE_DELAY", 0)
    monkeypatch.setattr(config, "TASK_RETRIES", 3)
    stub = RaisingProvider(ProviderTransientError("5xx"))
    with pytest.raises(LLMError, match="failed after 3 retries"):
        await LLM(providers={"openai_compat": stub})._call("s", "p", {}, tier=_tier())
    assert stub.calls == config.TASK_RETRIES + 1


class OkProvider(Provider):
    async def complete(self, system, prompt, schema, tier, *, fetch=False):
        return ({"action": "complete", "reasoning": "r",
                 "result": {"summary": "s", "output": "o", "facts": []}},
                {"input_tokens": 1, "output_tokens": 1, "model": tier["model"]})


async def test_engine_escalates_after_timeout(monkeypatch, tmp_path):
    tiers = dict(config.MODEL_TIERS)
    tiers["local"] = _tier("coder")
    tiers["local_reason"] = _tier("reasoner")
    monkeypatch.setattr(config, "MODEL_TIERS", tiers)
    monkeypatch.setattr(config, "RETRY_BASE_DELAY", 0)
    monkeypatch.setattr(config, "TASK_RETRIES", 3)
    monkeypatch.setattr(config, "ESCALATE_ON_FAILURE", True)
    db = Database(str(tmp_path / "t.sqlite3"))
    seed_prompts.seed(db)
    local = RaisingProvider(ProviderTimeoutError("local provider timed out after 7.0s"))
    engine = Engine(db, EventBus(), LLM(providers={"anthropic": OkProvider(),
                                                   "openai_compat": local}))
    run_id = db.create_run("req")
    task = db.create_task(run_id=run_id, parent_id=None, depth=0, seq=0,
                          role="r", title="t", prompt="p", tier="local_reason")
    decision, _ = await engine._decide(task)
    assert decision["action"] == "complete"
    assert local.calls == 1
    ev = [r for r in db.get_log(task["id"]) if r["event"] == "escalated"]
    assert len(ev) == 1
    assert "timed out" in json.dumps(ev[0], default=str)
    assert db.get_task(task["id"])["tier"] == "light"
