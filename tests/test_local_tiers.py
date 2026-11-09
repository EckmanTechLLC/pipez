import importlib
import json

import httpx2 as httpx
import pytest

from pipez import config, seed_prompts
from pipez.db import Database
from pipez.engine import Engine
from pipez.events import EventBus
from pipez.llm import LLM, MockLLM
from pipez.providers import LLMError, OpenAICompatProvider, Provider

LOCAL_VARS = [
    "PIPEZ_LOCAL_BASE_URL", "PIPEZ_LOCAL_MODEL", "PIPEZ_LOCAL_API_KEY",
    "PIPEZ_LOCAL_TIMEOUT", "PIPEZ_LOCAL_TEMPERATURE", "PIPEZ_LOCAL_THINKING",
    "PIPEZ_LOCAL_STAGES", "PIPEZ_LOCAL_REASONING_EFFORT",
    "PIPEZ_LOCAL_REASON_BASE_URL", "PIPEZ_LOCAL_REASON_MODEL",
    "PIPEZ_LOCAL_REASON_API_KEY", "PIPEZ_LOCAL_REASON_TIMEOUT",
    "PIPEZ_LOCAL_REASON_TEMPERATURE", "PIPEZ_LOCAL_REASON_THINKING",
    "PIPEZ_LOCAL_REASON_REASONING_EFFORT", "PIPEZ_LOCAL_REASON_STAGES",
]


@pytest.fixture
def cfg(monkeypatch):
    """Reload pipez.config under a controlled PIPEZ_LOCAL* environment."""
    # Blank (not delete) so config's .env loader (setdefault) cannot refill them.
    for v in LOCAL_VARS:
        monkeypatch.setenv(v, "")
    monkeypatch.setenv("PIPEZ_LOCAL_TIMEOUT", "600")
    monkeypatch.setenv("PIPEZ_LOCAL_TEMPERATURE", "0.2")

    def load(**env):
        for k in LOCAL_VARS:    # each call starts from a blank environment
            if k not in env:
                monkeypatch.setenv(k, "")
        monkeypatch.setenv("PIPEZ_LOCAL_TIMEOUT", env.get("PIPEZ_LOCAL_TIMEOUT", "600"))
        monkeypatch.setenv("PIPEZ_LOCAL_TEMPERATURE", env.get("PIPEZ_LOCAL_TEMPERATURE", "0.2"))
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        return importlib.reload(config)

    yield load
    monkeypatch.undo()
    importlib.reload(config)


BOTH = {"PIPEZ_LOCAL_BASE_URL": "http://coder.test", "PIPEZ_LOCAL_MODEL": "coder",
        "PIPEZ_LOCAL_API_KEY": "k-coder", "PIPEZ_LOCAL_TIMEOUT": "11",
        "PIPEZ_LOCAL_TEMPERATURE": "0.3",
        "PIPEZ_LOCAL_REASON_BASE_URL": "http://reason.test",
        "PIPEZ_LOCAL_REASON_MODEL": "reasoner",
        "PIPEZ_LOCAL_REASON_REASONING_EFFORT": "high"}


def test_no_local_vars(cfg):
    c = cfg()
    assert set(c.MODEL_TIERS) == {"deep", "standard", "light"}
    assert c.stage_tier("plan", "deep") == "deep"


def test_reason_only(cfg):
    c = cfg(PIPEZ_LOCAL_REASON_BASE_URL="http://r.test", PIPEZ_LOCAL_REASON_MODEL="r")
    assert set(c.MODEL_TIERS) == {"deep", "standard", "light", "local_reason"}
    assert c.next_tier_up("local") == "local_reason"
    assert c.PRICES_PER_MTOK["r"] == (0.0, 0.0)


def test_both_tiers_fields(cfg):
    c = cfg(**BOTH)
    co, re_ = c.MODEL_TIERS["local"], c.MODEL_TIERS["local_reason"]
    assert (co["base_url"], co["model"], co["api_key"]) == ("http://coder.test", "coder", "k-coder")
    assert co["timeout"] == 11.0 and co["temperature"] == 0.3
    assert co["reasoning_effort"] is None and co["thinking"] is False
    assert (re_["base_url"], re_["model"]) == ("http://reason.test", "reasoner")
    assert re_["reasoning_effort"] == "high"
    # unset key/timeout/temperature fall back to the local values
    assert (re_["api_key"], re_["timeout"], re_["temperature"]) == ("k-coder", 11.0, 0.3)
    assert re_["provider"] == "openai_compat" and "effort" in re_
    assert c.PRICES_PER_MTOK["coder"] == c.PRICES_PER_MTOK["reasoner"] == (0.0, 0.0)


def test_stage_tier_precedence(cfg):
    c = cfg(**BOTH, PIPEZ_LOCAL_REASON_STAGES="plan")
    assert c.stage_tier("plan", "deep") == "local_reason"
    assert c.stage_tier("intake", "deep") == "deep"
    c = cfg(**BOTH, PIPEZ_LOCAL_STAGES="plan")
    assert c.stage_tier("plan", "deep") == "local"
    c = cfg(**BOTH)
    assert c.stage_tier("plan", "deep") == "deep"


def test_offered_and_next_tier(cfg):
    c = cfg(**BOTH, PIPEZ_LOCAL_STAGES="decide", PIPEZ_LOCAL_REASON_STAGES="plan")
    assert "local" in c.offered_tiers("decide") and "local_reason" not in c.offered_tiers("decide")
    assert "local_reason" in c.offered_tiers("plan") and "local" not in c.offered_tiers("plan")
    assert c.local_allowed("decide") and not c.local_allowed("plan")
    assert c.next_tier_up("local") == "local_reason"
    assert c.next_tier_up("local_reason") == "light"
    c = cfg(PIPEZ_LOCAL_BASE_URL="http://c.test", PIPEZ_LOCAL_MODEL="c")
    assert c.next_tier_up("local") == "light"


# --- provider ---

def _prov(seen):
    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"},
                                                      "finish_reason": "stop"}],
                                         "usage": {}})
    return OpenAICompatProvider(transport=httpx.MockTransport(handler))


async def test_provider_uses_tier_settings():
    seen = {}
    tier = {"provider": "openai_compat", "model": "reasoner", "effort": "low",
            "base_url": "http://reason.test", "api_key": "k-r", "timeout": 5.0,
            "temperature": 0.7, "thinking": True, "reasoning_effort": "medium"}
    await _prov(seen).complete("s", "p", {"type": "object"}, tier)
    assert seen["url"] == "http://reason.test/v1/chat/completions"
    assert seen["auth"] == "Bearer k-r"
    assert seen["body"]["reasoning_effort"] == "medium"
    assert seen["body"]["chat_template_kwargs"] == {"enable_thinking": True}
    assert seen["body"]["temperature"] == 0.7


async def test_provider_local_has_no_reasoning_effort():
    seen = {}
    tier = {"provider": "openai_compat", "model": "coder", "effort": "low",
            "base_url": "http://coder.test", "api_key": "", "timeout": 5.0,
            "temperature": 0.2, "thinking": False, "reasoning_effort": None}
    await _prov(seen).complete("s", "p", {"type": "object"}, tier)
    assert "reasoning_effort" not in seen["body"]
    assert seen["body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert seen["auth"] is None


async def test_healthy_uses_tier_url_and_key():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={})

    p = OpenAICompatProvider(transport=httpx.MockTransport(handler))
    assert await p.healthy({"base_url": "http://reason.test", "api_key": "k"}) is True
    assert seen == {"url": "http://reason.test/v1/models", "auth": "Bearer k"}


# --- engine ---

def _tier(name, url):
    return {"provider": "openai_compat", "model": name, "effort": "low",
            "base_url": url, "api_key": "", "timeout": 1.0, "temperature": 0.2,
            "thinking": False, "reasoning_effort": None}


class FailingProvider(Provider):
    def __init__(self, down=()):
        self.down = set(down)
        self.calls = []

    async def complete(self, system, prompt, schema, tier, *, fetch=False):
        self.calls.append(tier["model"])
        raise LLMError("boom")

    async def healthy(self, tier=None):
        return tier["model"] not in self.down


class RecordingProvider(Provider):
    def __init__(self):
        self.tiers = []

    async def complete(self, system, prompt, schema, tier, *, fetch=False):
        self.tiers.append(tier)
        return ({"action": "complete", "reasoning": "r",
                 "result": {"summary": "s", "output": "o", "facts": []}},
                {"input_tokens": 1, "output_tokens": 1, "model": tier["model"]})


def _setup(monkeypatch, tmp_path, down=()):
    tiers = dict(config.MODEL_TIERS)
    tiers["local"] = _tier("coder", "http://c.test")
    tiers["local_reason"] = _tier("reasoner", "http://r.test")
    monkeypatch.setattr(config, "MODEL_TIERS", tiers)
    monkeypatch.setattr(config, "RETRY_BASE_DELAY", 0)
    monkeypatch.setattr(config, "TASK_RETRIES", 0)
    monkeypatch.setattr(config, "ESCALATE_ON_FAILURE", True)
    db = Database(str(tmp_path / "t.sqlite3"))
    seed_prompts.seed(db)
    anth, local = RecordingProvider(), FailingProvider(down)
    engine = Engine(db, EventBus(), LLM(providers={"anthropic": anth,
                                                   "openai_compat": local}))
    return db, engine, anth, local


def _task(db, tier):
    run_id = db.create_run("req")
    return db.create_task(run_id=run_id, parent_id=None, depth=0, seq=0,
                          role="r", title="t", prompt="p", tier=tier)


async def test_local_reason_failure_escalates_to_light(monkeypatch, tmp_path):
    db, engine, anth, local = _setup(monkeypatch, tmp_path)
    task = _task(db, "local_reason")
    await engine._probe_local("local_reason")
    assert engine._local_down == set()
    decision, _ = await engine._decide(task)
    assert decision["action"] == "complete"
    assert local.calls == ["reasoner"]
    assert [t["model"] for t in anth.tiers] == [config.MODEL_TIERS["light"]["model"]]
    ev = [r for r in db.get_log(task["id"]) if r["event"] == "escalated"]
    assert len(ev) == 1
    assert db.get_task(task["id"])["tier"] == "light"


async def test_probe_failure_is_per_tier(monkeypatch, tmp_path):
    db, engine, anth, local = _setup(monkeypatch, tmp_path, down=("reasoner",))
    await engine._probe_local("local")
    await engine._probe_local("local_reason")
    assert engine._local_down == {"local_reason"}
    t_reason, t_coder = _task(db, "local_reason"), _task(db, "local")
    await engine._decide(t_reason)
    assert local.calls == []        # reasoner never called
    assert db.get_task(t_reason["id"])["tier"] == "light"
    await engine._decide(t_coder)
    assert local.calls[0] == "coder"    # coder still tried first


def test_code_task_local_reason_coerced(monkeypatch, tmp_path):
    db, engine, _, _ = _setup(monkeypatch, tmp_path)
    run_id = db.create_run("req")
    db.set_plan(run_id, {"architecture": "a", "repo_layout": "l", "modules": [],
                         "tasks": [{"id": "t1", "title": "T", "module": "m",
                                    "prompt": "p", "tier": "local_reason",
                                    "acceptance": []}]}, {})
    db.set_brief(run_id, "brief", {})
    root = engine._materialise_plan(run_id)
    assert db.get_children(root["id"])[0]["tier"] == "light"
    ev = [r for r in db.get_log("run:%s" % run_id) if r["event"] == "tier_coerced"]
    assert len(ev) == 1
    assert ev[0]["data"]["from"] == "local_reason" if "data" in ev[0] else True
    assert engine._leaf_model({"tier": "local_reason"}) == config.MODEL_TIERS["light"]["model"]
