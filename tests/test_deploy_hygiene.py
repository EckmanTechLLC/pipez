"""Deploy hygiene (task 23): TTL expiry, keep flag, redeploy replaces."""
import time

import pytest
from fastapi.testclient import TestClient

from pipez import config
from pipez.db import Database
from pipez.engine import Engine
from pipez.events import EventBus
from pipez.llm import MockLLM

from tests.test_code_leaf import env, run_plan  # noqa: F401  (env is a fixture)
from tests.test_deploy import dep, finish  # noqa: F401  (dep is a fixture)


@pytest.fixture
def plain(tmp_path):
    db = Database(str(tmp_path / "h.sqlite3"))
    return db, Engine(db, EventBus(), MockLLM())


def ready_row(db, rid, **kw):
    fields = dict(status="ready", url="http://x/" + rid, name=str(rid) + "-nope",
                  ready_at=time.time(), keep=0, expires_at=None)
    fields.update(kw)
    return db.upsert_deployment(rid, **fields)


async def test_ready_deployment_gets_expiry(dep, monkeypatch):
    db, engine, cfg = dep
    monkeypatch.setattr(config, "DEPLOY_TTL_HOURS", 2)
    rid, _ = await run_plan(db, engine)
    d = await finish(db, rid, ("ready", "failed"))
    assert d["status"] == "ready", d["error"]
    assert abs(d["expires_at"] - (time.time() + 2 * 3600)) < 120
    assert d["keep"] == 0
    await engine.undeploy(rid)


async def test_ttl_zero_means_no_expiry(dep, monkeypatch):
    db, engine, cfg = dep
    monkeypatch.setattr(config, "DEPLOY_TTL_HOURS", 0)
    rid, _ = await run_plan(db, engine)
    d = await finish(db, rid, ("ready", "failed"))
    assert d["status"] == "ready" and d["expires_at"] is None
    await engine.undeploy(rid)


async def test_expire_undeploys_only_expired_non_keep(plain):
    db, engine = plain
    a, b, c = (db.create_run("r%d" % i) for i in range(3))
    ready_row(db, a, expires_at=time.time() - 10)
    ready_row(db, b, expires_at=time.time() - 10, keep=1)
    ready_row(db, c, expires_at=time.time() + 3600)
    report = await engine.expire_deployments()
    assert [r["run_id"] for r in report] == [a] and report[0]["expired"]
    assert db.get_deployment(a)["status"] == "destroyed"
    assert db.get_deployment(b)["status"] == "ready"
    assert db.get_deployment(c)["status"] == "ready"
    assert any(e["event"] == "deploy_expired" for e in db.get_log("run:" + a))


async def test_set_deploy_keep_toggles_expiry(plain, monkeypatch):
    db, engine = plain
    monkeypatch.setattr(config, "DEPLOY_TTL_HOURS", 1)
    rid = db.create_run("r")
    ready_row(db, rid, expires_at=time.time() + 5)
    row = engine.set_deploy_keep(rid, True)
    assert row["keep"] == 1 and row["expires_at"] is None
    row = engine.set_deploy_keep(rid, False)
    assert row["keep"] == 0 and abs(row["expires_at"] - (time.time() + 3600)) < 60
    assert [e["event"] for e in db.get_log("run:" + rid)].count("deploy_keep") == 2
    with pytest.raises(ValueError):
        engine.set_deploy_keep("nope", True)


async def test_child_ready_replaces_parent_not_kept(plain, monkeypatch):
    db, engine = plain
    parent = db.create_run("p")
    child = db.create_run("c", None, parent)
    other = db.create_run("unrelated")
    ready_row(db, parent)
    ready_row(db, other)
    ready_row(db, child)
    assert await engine.replace_deployments(child) == [parent]
    assert db.get_deployment(parent)["status"] == "destroyed"
    assert db.get_deployment(other)["status"] == "ready"
    assert db.get_deployment(child)["status"] == "ready"
    assert any(e["event"] == "deploy_replaced" for e in db.get_log("run:" + child))
    # kept parent survives; replace off replaces nothing
    p2 = db.create_run("p2")
    c2 = db.create_run("c2", None, p2)
    ready_row(db, p2, keep=1)
    ready_row(db, c2)
    assert await engine.replace_deployments(c2) == []
    assert db.get_deployment(p2)["status"] == "ready"
    p3 = db.create_run("p3")
    c3 = db.create_run("c3", None, p3)
    ready_row(db, p3)
    ready_row(db, c3)
    monkeypatch.setattr(config, "DEPLOY_REPLACE", False)
    assert await engine.replace_deployments(c3) == []
    assert db.get_deployment(p3)["status"] == "ready"


def test_routes_keep_and_expire(tmp_path):
    from pipez import server
    db = Database(str(tmp_path / "api.sqlite3"))
    engine = Engine(db, EventBus(), MockLLM())
    server.db, server.engine = db, engine
    a, b = db.create_run("a"), db.create_run("b")
    ready_row(db, a, expires_at=time.time() - 5)
    ready_row(db, b, expires_at=time.time() - 5)
    client = TestClient(server.app)
    r = client.put("/api/runs/%s/deployment/keep" % b, json={"keep": True})
    assert r.status_code == 200 and r.json()["keep"] == 1
    assert client.put("/api/runs/nope/deployment/keep", json={"keep": True}).status_code == 404
    rep = client.post("/api/deployments/expire").json()
    assert [x["run_id"] for x in rep] == [a]
    rows = {d["run_id"]: d for d in client.get("/api/deployments").json()}
    assert rows[a]["status"] == "destroyed" and rows[b]["status"] == "ready"
