import asyncio
import os

import pytest
from fastapi.testclient import TestClient

from pipez import config, seed_prompts
from pipez.db import Database
from pipez.engine import Engine
from pipez.events import EventBus
from pipez.llm import LLMError, MockLLM
from pipez.prefs import PreferenceStore
from pipez.sandbox import MockSandbox

from tests.test_intake import _fast_sleep, _real_sleep, wait_status


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_ARTIFACTS", False)
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
    db = Database(str(tmp_path / "p.sqlite3"))
    seed_prompts.seed(db)
    prefs = PreferenceStore(db)
    return db, Engine(db, EventBus(), MockLLM(), prefs)


async def to_plan(db, engine):
    rid = engine.submit("build a thing")
    await wait_status(db, rid, ("awaiting_human",))
    engine.answer_intake(rid, accept_all=True)
    run = await wait_status(db, rid, ("awaiting_human", "failed"))
    openk = next(d["key"] for d in run["decisions"] if d.get("free_text"))
    engine.answer_intake(rid, {openk: "Pipez Demo"})
    run = await wait_status(db, rid, ("awaiting_plan", "failed"))
    assert run["status"] == "awaiting_plan", run["error"]
    return rid, run


async def test_full_flow(env):
    db, engine = env
    rid, run = await to_plan(db, engine)
    assert [t["id"] for t in run["plan"]["tasks"]] == ["t1", "t2", "t3"]
    assert db.get_tasks(rid) == []
    with pytest.raises(ValueError):
        engine.resume(rid)
    with pytest.raises(ValueError, match="note"):
        engine.answer_plan(rid, False)
    with pytest.raises(ValueError, match="note"):
        engine.answer_plan(rid, False, note="  ")
    assert db.get_run(rid)["status"] == "awaiting_plan"

    engine.answer_plan(rid, False, note="tighter")
    assert db.get_run(rid)["status"] == "planning"
    run = await wait_status(db, rid, ("awaiting_plan", "failed"))
    assert run["status"] == "awaiting_plan"
    assert run["plan_revisions"] == 1
    assert run["plan"]["architecture"].startswith("Revised: ")
    assert run["plan_meta"]["revisions"][0]["note"] == "tighter"

    engine.answer_plan(rid, True)
    assert db.get_run(rid)["status"] == "provisioning"
    with pytest.raises(ValueError):
        engine.answer_plan(rid, True)
    run = await wait_status(db, rid, ("done", "failed"))
    assert run["status"] == "done", run["error"]

    tasks = db.get_tasks(rid)
    root = next(t for t in tasks if not t["parent_id"])
    kids = {t["title"]: t for t in tasks if t["parent_id"]}
    assert root["role"] == "integrator" and root["kind"] == "llm"
    assert len(kids) == 3 and all(k["kind"] == "code" for k in kids.values())
    assert "CONTRACT-CORE" in root["context"] and "CONTRACT-API" in root["context"]
    assert root["context"].startswith(run["brief"])
    t1, t2, t3 = (kids[n] for n in ("Core data model", "Core logic", "API endpoint"))
    assert t3["depends_on"] == [t1["id"]] and t1["depends_on"] == []
    assert t2["effort"] == "low" and t1["effort"] is None
    assert t3["module"] == "api" and t3["role"] == "api-engineer"
    assert "CONTRACT-API" in t3["prompt"]
    assert t2["acceptance"] == ["run() returns a Result", "unit tests pass"]
    assert "runner" in [l["event"] for l in db.get_log(t1["id"])]


async def test_resume_planning_replans(env):
    db, engine = env
    rid, _ = await to_plan(db, engine)
    db.set_run_status(rid, "planning")
    engine.resume(rid)
    run = await wait_status(db, rid, ("awaiting_plan", "failed"))
    assert run["status"] == "awaiting_plan"


async def test_validation_drops_bad_edges(env):
    db, engine = env
    rid, run = await to_plan(db, engine)
    plan = run["plan"]
    plan["tasks"][0]["depends_on"] = ["t3", "zzz"]       # t3 -> t1 closes a cycle
    plan["tasks"].append(dict(plan["tasks"][0], depends_on=[]))   # duplicate id
    plan["modules"][0]["depends_on"] = ["nope"]
    clean = engine._validate_plan(rid, plan)
    assert len(clean["tasks"]) == 3
    assert clean["tasks"][0]["depends_on"] in (["t3"], [])
    assert not (clean["tasks"][0]["depends_on"] and clean["tasks"][2]["depends_on"])
    assert clean["modules"][0]["depends_on"] == []
    assert "plan_invalid" in [l["event"] for l in db.get_log("run:%s" % rid)]
    with pytest.raises(LLMError):
        engine._validate_plan(rid, dict(plan, tasks=[]))


async def test_approve_with_edited_plan(env):
    db, engine = env
    rid, run = await to_plan(db, engine)
    plan = run["plan"]
    plan["tasks"] = plan["tasks"][:2]
    engine.answer_plan(rid, True, plan=plan)
    run = await wait_status(db, rid, ("done", "failed"))
    assert run["status"] == "done", run["error"]
    assert len(db.get_tasks(rid)) == 3  # root + 2
    # empty edited plan is a 400-class error and leaves the gate open
    rid2, _ = await to_plan(db, engine)
    with pytest.raises(ValueError):
        engine.answer_plan(rid2, True, plan={"tasks": []})
    assert db.get_run(rid2)["status"] == "awaiting_plan"


def test_api_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MOCK", True)
    monkeypatch.setattr(config, "ENABLE_ARTIFACTS", False)
    from pipez import server
    server.db = Database(str(tmp_path / "api.sqlite3"))
    seed_prompts.seed(server.db)
    server.prefs = PreferenceStore(server.db)
    import time
    with TestClient(server.app) as c:
        server.engine = Engine(server.db, server.bus, MockLLM(), server.prefs)
        rid = c.post("/api/requests", json={"request": "hello"}).json()["run_id"]

        def wait(want):
            for _ in range(200):
                p = c.get("/api/runs/%s/plan" % rid).json()
                if p["status"] == want:
                    return p
                time.sleep(0.05)
            raise AssertionError(p["status"])
        wait("awaiting_human")
        assert c.post("/api/runs/%s/plan" % rid, json={"approve": True}).status_code == 400
        c.post("/api/runs/%s/intake" % rid, json={"accept_all": True})
        i = c.get("/api/runs/%s/intake" % rid).json()
        for _ in range(200):
            if i["status"] == "awaiting_human":
                break
            time.sleep(0.05)
            i = c.get("/api/runs/%s/intake" % rid).json()
        openk = next(d["key"] for d in i["decisions"] if d.get("free_text"))
        c.post("/api/runs/%s/intake" % rid, json={"answers": {openk: "x"}})
        p = wait("awaiting_plan")
        assert len(p["plan"]["tasks"]) == 3
        assert c.post("/api/runs/%s/plan" % rid, json={"approve": False}).status_code == 400
        c.post("/api/runs/%s/plan" % rid, json={"approve": False, "note": "x"})
        p = wait("awaiting_plan")
        assert p["plan_revisions"] == 1


def _statuses(bus):
    q = bus.subscribe()

    def drain():
        out = []
        while not q.empty():
            ev = q.get_nowait()
            if ev["type"] == "run" and (not out or out[-1] != ev["run"]["status"]):
                out.append(ev["run"]["status"])
        return out
    return drain


async def test_sandbox_lifecycle_destroyed_on_success(env):
    db, engine = env
    drain = _statuses(engine.bus)
    rid, _ = await to_plan(db, engine)
    engine.answer_plan(rid, True)
    run = await wait_status(db, rid, ("done", "failed"))
    assert run["status"] == "done", run["error"]
    seq = drain()
    assert seq.index("provisioning") < seq.index("running") < seq.index("done")
    sb = db.get_sandbox(rid)
    assert sb["status"] == "destroyed" and sb["kind"] == "mock" and sb["kept"] == 0
    assert sb["ready_at"] and sb["destroyed_at"]
    assert not os.path.exists(sb["name"])
    assert [r["run_id"] for r in db.list_sandboxes("destroyed")] == [rid]

    from pipez import server
    server.db, server.engine = db, engine
    client = TestClient(server.app)
    assert client.get("/api/runs/%s/sandbox" % rid).json()["status"] == "destroyed"
    assert [r["run_id"] for r in client.get("/api/sandboxes").json()] == [rid]
    assert client.post("/api/sandboxes/%s/destroy" % rid).status_code == 400
    assert client.post("/api/sandboxes/nope/destroy").status_code == 404


class FailingLLM(MockLLM):
    async def decide(self, *a, **k):
        raise LLMError("boom")

    async def synthesize(self, *a, **k):
        raise LLMError("boom")


async def test_failed_run_keeps_sandbox_then_resume_reuses_and_destroys(env):
    db, engine = env
    rid, _ = await to_plan(db, engine)
    good = engine.llm
    engine.llm = FailingLLM()
    engine.answer_plan(rid, True)
    run = await wait_status(db, rid, ("done", "failed"))
    assert run["status"] == "failed"
    await asyncio.sleep(0)
    for _ in range(200):
        if db.get_sandbox(rid)["status"] == "kept":
            break
        await _real_sleep(0.01)
    sb = db.get_sandbox(rid)
    assert sb["status"] == "kept" and sb["kept"] == 1
    assert os.path.isdir(sb["name"])
    assert "sandbox_kept" in [l["event"] for l in db.get_log("run:" + rid)]

    engine.llm = good
    engine.resume(rid)
    run = await wait_status(db, rid, ("done", "failed"))
    assert run["status"] == "done", run["error"]
    for _ in range(200):
        if db.get_sandbox(rid)["status"] == "destroyed":
            break
        await _real_sleep(0.01)
    assert db.get_sandbox(rid)["name"] == sb["name"]   # same sandbox reused
    assert db.get_sandbox(rid)["status"] == "destroyed"
    assert "sandbox_reused" in [l["event"] for l in db.get_log("run:" + rid)]


async def test_manual_destroy_of_kept(env):
    db, engine = env
    rid, _ = await to_plan(db, engine)
    engine.llm = FailingLLM()
    engine.answer_plan(rid, True)
    await wait_status(db, rid, ("failed",))
    for _ in range(200):
        if db.get_sandbox(rid)["status"] == "kept":
            break
        await _real_sleep(0.01)
    path = db.get_sandbox(rid)["name"]
    row = await engine.destroy_sandbox(rid)
    assert row["status"] == "destroyed" and not os.path.exists(path)


class BigPlanLLM(MockLLM):
    async def plan(self, system, prompt, tier=None):
        self.system = system
        self.tier = tier
        out, usage = await super().plan(system, prompt, tier)
        t = out["tasks"]
        out["tasks"] = [dict(t[0], id="t%d" % i, depends_on=[]) for i in range(13)]
        return out, usage


async def test_plan_oversize_warning(env):
    db, _ = env
    llm = BigPlanLLM()
    engine = Engine(db, EventBus(), llm, PreferenceStore(db))
    rid, run = await to_plan(db, engine)
    assert len(run["plan"]["tasks"]) == 13
    assert any("13 tasks" in w for w in run["plan_meta"]["warnings"])
    assert [e for e in db.get_log("run:%s" % rid) if e["event"] == "plan_oversize"]
    assert "HARD LIMITS: at most 12 tasks and at most 8 modules" in llm.system
    assert llm.tier["effort"] == config.PLAN_EFFORT
