import json

import pytest

from pipez import config, seed_prompts
from pipez.db import Database
from pipez.engine import Engine
from pipez.events import EventBus
from pipez.llm import LLM
from pipez.providers import LLMError, Provider


class FailingProvider(Provider):
    async def complete(self, system, prompt, schema, tier, *, fetch=False):
        raise LLMError("boom")

    async def healthy(self, tier=None):
        return True


class RecordingProvider(Provider):
    def __init__(self):
        self.tiers = []

    async def complete(self, system, prompt, schema, tier, *, fetch=False):
        self.tiers.append(tier)
        return ({"action": "complete", "reasoning": "r",
                 "result": {"summary": "s", "output": "o", "facts": []}},
                {"input_tokens": 1, "output_tokens": 1, "model": tier["model"]})


async def test_local_failure_escalates_to_light(monkeypatch, tmp_path):
    tiers = dict(config.MODEL_TIERS)
    tiers["local"] = {"provider": "openai_compat", "model": "qwen3",
                      "effort": "low", "base_url": "http://x"}
    tiers.pop("local_reason", None)  # independent of a local_reason tier in .env
    monkeypatch.setattr(config, "MODEL_TIERS", tiers)
    monkeypatch.setattr(config, "LOCAL_BASE_URL", "")  # no health probe
    monkeypatch.setattr(config, "RETRY_BASE_DELAY", 0)
    monkeypatch.setattr(config, "TASK_RETRIES", 0)
    monkeypatch.setattr(config, "ESCALATE_ON_FAILURE", True)

    db = Database(str(tmp_path / "t.sqlite3"))
    seed_prompts.seed(db)
    anthropic_stub = RecordingProvider()
    llm = LLM(providers={"anthropic": anthropic_stub,
                         "openai_compat": FailingProvider()})
    engine = Engine(db, EventBus(), llm)

    run_id = db.create_run("req")
    task = db.create_task(run_id=run_id, parent_id=None, depth=0, seq=0,
                          role="r", title="t", prompt="p", tier="local")
    await engine._probe_local()
    assert engine._local_down == set()
    decision, usage = await engine._decide(task)

    assert decision["action"] == "complete"
    assert [t["model"] for t in anthropic_stub.tiers] == [tiers["light"]["model"]]
    assert db.get_task(task["id"])["tier"] == "light"
    events = [r for r in db.get_log(task["id"]) if r["event"] == "escalated"]
    assert len(events) == 1
    detail = json.loads(events[0]["detail"])
    assert (detail["from"], detail["to"], detail["reason"]) == ("local", "light", "boom")
