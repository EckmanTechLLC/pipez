import pytest
from fastapi.testclient import TestClient

from pipez import config, seed_prompts
from pipez.db import Database
from pipez.prefs import PreferenceStore

from tests.test_plan import env, to_plan  # noqa: F401
from tests.test_intake import wait_status


def test_estimate_cost():
    assert config.estimate_cost("claude-opus-5-5", 1_000_000, 1_000_000) == pytest.approx(24.0)
    assert config.estimate_cost("claude-sonnet-5-5", 1_000_000, 100_000) == pytest.approx(3.0)
    assert config.estimate_cost("claude-haiku-5-5", 1_000_000, 1_000_000) == pytest.approx(0.6)
    assert config.estimate_cost("no-such-model", 5000, 5000) == 0.0


async def test_run_level_spend_rows(env, monkeypatch):
    monkeypatch.setitem(config.PRICES_PER_MTOK, "mock", (10.0, 50.0))
    db, engine = env
    rid, run = await to_plan(db, engine)
    rows = db._query("SELECT * FROM spend WHERE run_id=?", (rid,))
    stages = {r["stage"] for r in rows}
    assert {"intake", "brief", "plan"} <= stages
    assert all(r["source"] == "estimate" and r["cost_usd"] > 0 for r in rows)
    assert db.get_run(rid)["cost_usd"] == pytest.approx(sum(r["cost_usd"] for r in rows))
    sp = db.get_spend(rid)
    assert sp["total_usd"] == pytest.approx(db.get_run(rid)["cost_usd"])
    assert sp["by_stage"]["plan"]["calls"] >= 1


async def test_leaf_reported_spend_and_api(env, tmp_path, monkeypatch):
    db, engine = env
    rid, run = await to_plan(db, engine)
    engine.answer_plan(rid, True)
    run = await wait_status(db, rid, ("done", "failed"))
    assert run["status"] == "done", run["error"]
    leaf = db._query("SELECT * FROM spend WHERE run_id=? AND stage='leaf'", (rid,))
    assert leaf and all(r["source"] == "reported" for r in leaf)
    tasks = {t["id"]: t for t in db.get_tasks(rid)}
    for r in leaf:
        assert r["cost_usd"] == pytest.approx(tasks[r["task_id"]]["cost_usd"])
    assert db.get_run(rid)["cost_usd"] >= sum(r["cost_usd"] for r in leaf)


def test_api_spend(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MOCK", True)
    from pipez import server
    server.db = Database(str(tmp_path / "api.sqlite3"))
    seed_prompts.seed(server.db)
    server.prefs = PreferenceStore(server.db)
    rid = server.db.create_run("x")
    server.db.add_spend(rid, None, "plan", "m", 10, 5, 0.5)
    server.db.add_spend(rid, None, "leaf", "m", 1, 1, 0.25, "reported")
    with TestClient(server.app) as c:
        assert c.get("/api/runs/nope/spend").status_code == 404
        j = c.get("/api/runs/%s/spend" % rid).json()
        assert j["total_usd"] == pytest.approx(0.75)
        assert j["by_stage"]["plan"] == {"usd": 0.5, "calls": 1,
                                         "input_tokens": 10, "output_tokens": 5}
        assert j["by_model"]["m"]["calls"] == 2
        assert c.get("/api/runs/%s" % rid).json()["cost_usd"] == pytest.approx(0.75)
        pid = server.db.get_run(rid)["project_id"]
        assert c.get("/api/projects/%s/spend" % pid).json()["total_usd"] == pytest.approx(0.75)
