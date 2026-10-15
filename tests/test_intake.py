import json
import asyncio

import pytest
from fastapi.testclient import TestClient

from pipez import config, seed_prompts
from pipez.db import Database
from pipez.engine import Engine
from pipez.events import EventBus
from pipez.llm import MockLLM
from pipez.prefs import PreferenceStore


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_ARTIFACTS", False)
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
    db = Database(str(tmp_path / "i.sqlite3"))
    seed_prompts.seed(db)
    prefs = PreferenceStore(db)
    return db, prefs, Engine(db, EventBus(), MockLLM(), prefs)


_real_sleep = asyncio.sleep


async def _fast_sleep(t, *a):
    await _real_sleep(0)


async def wait_status(db, run_id, want, tries=6000):
    for _ in range(tries):
        if db.get_run(run_id)["status"] in want:
            return db.get_run(run_id)
        await _real_sleep(0.005)
    raise AssertionError("run stuck in %s" % db.get_run(run_id)["status"])


async def test_submit_pauses_with_decisions(env):
    db, prefs, engine = env
    rid = engine.submit("build a thing")
    assert db.get_run(rid)["status"] == "intake"
    run = await wait_status(db, rid, ("awaiting_human",))
    assert len(run["decisions"]) == 3
    lic = next(d for d in run["decisions"] if d["key"] == "license")
    assert lic["prefill"]["source"] == "model" and lic["prefill"]["value"] == "MIT"
    assert db.get_tasks(rid) == []


async def test_global_prefill(env):
    db, prefs, engine = env
    for _ in range(3):
        prefs.observe("storage.database", "postgres", "global", "intake")
        await _real_sleep(0.002)
    rid = engine.submit("x")
    run = await wait_status(db, rid, ("awaiting_human",))
    d = next(d for d in run["decisions"] if d["key"] == "storage.database")
    assert d["prefill"]["source"] == "global" and d["prefill"]["value"] == "postgres"


async def test_missing_required(env):
    db, prefs, engine = env
    rid = engine.submit("x")
    await wait_status(db, rid, ("awaiting_human",))
    with pytest.raises(ValueError, match="license"):
        engine.answer_intake(rid, {"hosting.target": "vm"})
    assert db.get_run(rid)["status"] == "awaiting_human"


async def test_accept_all_flow(env):
    db, prefs, engine = env
    for _ in range(3):
        prefs.observe("storage.database", "postgres", "global", "intake")
        await _real_sleep(0.002)
    rid = engine.submit("x")
    await wait_status(db, rid, ("awaiting_human",))
    engine.answer_intake(rid, {"storage.database": "sqlite"},
                         {"storage.database": "embedded only"}, accept_all=True)
    run = await wait_status(db, rid, ("awaiting_human", "failed"))
    openk = next(d["key"] for d in run["decisions"] if d.get("free_text"))
    engine.answer_intake(rid, {openk: "Pipez Demo"})
    run = await wait_status(db, rid, ("awaiting_plan", "failed"))
    assert run["status"] == "awaiting_plan", run["error"]
    assert db.get_tasks(rid) == []
    engine.answer_plan(rid, True)
    run = await wait_status(db, rid, ("done", "failed"))
    assert run["status"] == "done", run["error"]
    obs = {o["key"]: o for o in prefs.observations(limit=100)
           if o["run_id"] == rid}
    assert obs["license"]["source"] == "intake" and obs["license"]["value"] == "MIT"
    assert obs["storage.database"]["source"] == "override"
    assert obs["storage.database"]["reason"] == "embedded only"
    assert obs["hosting.target"]["source"] == "intake"
    assert "| key |" in run["brief"] and "| license |" in run["brief"]
    root = next(t for t in db.get_tasks(rid) if not t["parent_id"])
    assert root["context"].startswith(run["brief"])


async def test_resume_awaiting_human(env):
    db, prefs, engine = env
    rid = engine.submit("x")
    await wait_status(db, rid, ("awaiting_human",))
    with pytest.raises(ValueError):
        engine.resume(rid)


def test_api_intake(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPEZ_DB", str(tmp_path / "api.sqlite3"))
    monkeypatch.setattr(config, "MOCK", True)
    from pipez import server
    server.db = Database(str(tmp_path / "api.sqlite3"))
    seed_prompts.seed(server.db)
    server.prefs = PreferenceStore(server.db)
    with TestClient(server.app) as c:
        server.engine = Engine(server.db, server.bus, MockLLM(), server.prefs)
        r = c.post("/api/requests", json={"request": "hello"}).json()
        assert r["status"] == "intake"
        import time
        for _ in range(100):
            i = c.get("/api/runs/%s/intake" % r["run_id"]).json()
            if i["status"] == "awaiting_human":
                break
            time.sleep(0.05)
        assert len(i["decisions"]) == 3
        bad = c.post("/api/runs/%s/intake" % r["run_id"], json={"answers": {}})
        assert bad.status_code == 400 and "license" in bad.json()["detail"]


async def test_open_question_round_trip(env):
    db, prefs, engine = env
    rid = engine.submit("x")
    await wait_status(db, rid, ("awaiting_human",))
    engine.answer_intake(rid, accept_all=True)
    run = await wait_status(db, rid, ("awaiting_human", "failed"))
    assert run["status"] == "awaiting_human", run["error"]
    opens = [d for d in run["decisions"] if d["key"].startswith("open.")]
    assert len(opens) == 1
    o = opens[0]
    assert o["free_text"] and o["required"] and o["options"] == []
    assert o["why_it_matters"] == "The brief could not settle this"
    assert run["brief"] is None
    with pytest.raises(ValueError, match="open\\."):
        engine.answer_intake(rid, accept_all=True)
    engine.answer_intake(rid, {o["key"]: "Pipez Demo"})
    run = await wait_status(db, rid, ("awaiting_plan", "failed"))
    assert run["status"] == "awaiting_plan", run["error"]
    assert "Resolved open questions" in run["brief"]
    assert "Pipez Demo" in run["brief"]
    obs = [x for x in prefs.observations(limit=100) if x["key"] == o["key"]]
    assert len(obs) == 1 and obs[0]["scope"] == "request"
    assert obs[0]["value"] == "Pipez Demo"
    # earlier decisions were observed once only
    assert len([x for x in prefs.observations(limit=100)
                if x["key"] == "license"]) == 1


async def test_last_round_becomes_assumptions(env, monkeypatch):
    db, prefs, engine = env
    monkeypatch.setattr(config, "INTAKE_MAX_ROUNDS", 1)
    rid = engine.submit("x")
    await wait_status(db, rid, ("awaiting_human",))
    engine.answer_intake(rid, accept_all=True)
    run = await wait_status(db, rid, ("awaiting_plan", "failed"))
    assert run["status"] == "awaiting_plan", run["error"]
    assert "## Assumptions" in run["brief"]
    assert not [d for d in run["decisions"] if d["key"].startswith("open.")]


class OneOptionLLM(MockLLM):
    async def intake(self, system, prompt, tier=None):
        out, usage = await super().intake(system, prompt, tier)
        out["request_values"] = {"license": "MIT"}
        out["decisions"][1]["options"] = out["decisions"][1]["options"][:1]
        out["decisions"][0]["options"] = out["decisions"][0]["options"][:1]
        return out, usage


async def test_locked_exempt_from_invalid(env):
    db, prefs, _ = env
    engine = Engine(db, EventBus(), OneOptionLLM(), prefs)
    rid = engine.submit("x")
    await wait_status(db, rid, ("awaiting_human",))
    invalid = [e for e in db.get_log("run:%s" % rid) if e["event"] == "intake_invalid"]
    keys = [json.loads(e["detail"])["key"] for e in invalid]
    assert keys == ["storage.database"]


class CaptureLLM(MockLLM):
    prompts = []

    async def intake(self, system, prompt, tier=None):
        self.prompts.append(prompt)
        return await super().intake(system, prompt, tier)


async def test_known_keys_in_discovery_prompt(env):
    db, prefs, _ = env
    for _ in range(3):
        prefs.observe("storage.database", "postgres", "global", "intake")
        await _real_sleep(0.002)
    prefs.observe("open.some_thing", "x", "request", "intake")
    llm = CaptureLLM()
    llm.prompts = []
    engine = Engine(db, EventBus(), llm, prefs)
    rid = engine.submit("x")
    await wait_status(db, rid, ("awaiting_human",))
    p = llm.prompts[0]
    assert "# Known decision keys" in p
    assert "- storage.database = postgres (global, n=3)" in p
    assert "- runtime.language" in p and "open.some_thing" not in p
