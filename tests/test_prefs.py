import sqlite3
import time

import pytest

from pipez import config
from pipez.db import Database
from pipez.prefs import PreferenceStore

K = "storage.database"


@pytest.fixture
def store(tmp_path):
    return PreferenceStore(Database(str(tmp_path / "p.sqlite3")))


def obs(store, value, n=1, scope="global", source="intake", project_id=None,
        reason=None):
    for _ in range(n):
        store.observe(K, value, scope, source, project_id=project_id, reason=reason)
        time.sleep(0.002)


def test_none(store):
    r = store.resolve(K)
    assert r["source"] == "none" and r["value"] is None
    assert r["evidence"]["count"] == 0


def test_graduation_at_three(store):
    obs(store, "postgres", 2)
    assert store.resolve(K)["source"] == "none"
    obs(store, "postgres")
    r = store.resolve(K)
    assert (r["source"], r["value"], r["evidence"]["count"]) == ("global", "postgres", 3)


def test_exception_does_not_erode(store):
    obs(store, "postgres", 3)
    obs(store, "mssql", source="override", reason="client mandated")
    r = store.resolve(K)
    assert (r["source"], r["value"]) == ("global", "postgres")
    assert [e["value"] for e in r["evidence"]["exceptions"]] == ["mssql"]
    assert r["evidence"]["exceptions"][0]["reason"] == "client mandated"


def test_default_flips_within_window(store):
    obs(store, "postgres", 3)
    obs(store, "sqlite", 3)
    assert store.resolve(K)["value"] == "sqlite"


def test_project_precedence(store):
    obs(store, "postgres", 3, project_id="other")
    obs(store, "sqlite", project_id="P", scope="project")
    assert store.resolve(K, "P")["source"] == "project"
    assert store.resolve(K, "P")["value"] == "sqlite"
    r = store.resolve(K, "Q")
    assert (r["source"], r["value"]) == ("global", "postgres")


def test_request_pin_retire(store):
    obs(store, "postgres", 3)
    obs(store, "sqlite", project_id="P", scope="project")
    assert store.resolve(K, "P", {K: "mysql"})["source"] == "request"
    store.pin(K, "oracle")
    r = store.resolve(K, "P")
    assert (r["source"], r["value"]) == ("pinned", "oracle")
    assert store.resolve(K, "P", {K: "mysql"})["value"] == "mysql"
    store.retire(K)
    assert store.resolve(K, "Z")["source"] == "none"
    time.sleep(0.002)
    obs(store, "postgres", 1)
    assert store.resolve(K, "Z")["source"] == "global"
    assert K in store.resolve_all()


def test_request_scope_not_a_preference(store):
    obs(store, "sqlite", 3, scope="request", source="request")
    assert store.resolve(K)["source"] == "none"
    assert len(store.observations(K)) == 3


def test_invalid_enum(store):
    with pytest.raises(ValueError):
        store.observe(K, "x", "bogus", "intake")


def test_run_lineage(tmp_path):
    db = Database(str(tmp_path / "r.sqlite3"))
    a = db.create_run("a")
    b = db.create_run("b", parent_run_id=a)
    c = db.create_run("c", parent_run_id=b)
    assert db.get_run(a)["project_id"] == a
    assert db.get_run(b)["project_id"] == a == db.get_run(c)["project_id"]


def test_backfill(tmp_path):
    path = str(tmp_path / "old.sqlite3")
    db = Database(path)
    a, b, c = db.create_run("a"), db.create_run("b"), db.create_run("c")
    db._exec("UPDATE runs SET parent_run_id=? WHERE id=?", (a, b))
    db._exec("UPDATE runs SET parent_run_id=? WHERE id=?", (b, c))
    db._exec("UPDATE runs SET project_id=NULL")
    db._conn.close()
    db2 = Database(path)
    assert [db2.get_run(x)["project_id"] for x in (a, b, c)] == [a, a, a]


def test_api_smoke(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from pipez import server
    monkeypatch.setattr(server, "db", Database(str(tmp_path / "s.sqlite3")))
    monkeypatch.setattr(server, "prefs", PreferenceStore(server.db))
    c = TestClient(server.app)
    body = {"key": K, "value": "postgres", "scope": "global", "source": "intake"}
    for _ in range(3):
        assert c.post("/api/observations", json=body).status_code == 200
    assert c.post("/api/observations", json=dict(body, scope="nope")).status_code == 400
    r = c.get("/api/preferences/" + K).json()
    assert (r["source"], r["value"]) == ("global", "postgres")
    assert c.put("/api/preferences/" + K, json={"value": "x"}).status_code == 200
    assert c.get("/api/preferences/" + K).json()["source"] == "pinned"
    assert c.get("/api/runs/nope").status_code == 404
