"""Deploy to a lab VM, mock flavour: the deploy 'VM' is a temp dir and the
service a local background process (no Proxmox, no systemd)."""
import json
import socket
import urllib.request

import pytest

from pipez import config
from pipez.deploy import app_name, systemd_unit
from pipez.engine import Engine

from tests.test_code_leaf import env, run_plan  # noqa: F401  (env is a fixture)
from tests.test_intake import _real_sleep, wait_status


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def port_open(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture
def dep(env, tmp_path, monkeypatch):
    db, engine, llm, _ = env
    monkeypatch.setattr(config, "ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setattr(config, "DEPLOY_AUTO", True)
    cfg = {"target": "vm", "install_commands": ["sudo apt-get install -y python3", "true"],
           "start_command": "python3 -m http.server {port}", "port": free_port(),
           "health_path": "/", "env": []}
    orig = llm.plan

    async def plan(system, prompt, tier=None):
        out, usage = await orig(system, prompt, tier)
        out["deploy"] = cfg
        return out, usage
    llm.plan = plan
    yield db, engine, cfg


async def finish(db, rid, want):
    for _ in range(3000):
        d = db.get_deployment(rid)
        if d and d["status"] in want:
            return d
        await _real_sleep(0.005)
    raise AssertionError("deployment stuck: %s" % db.get_deployment(rid))


async def test_auto_deploy_ready_then_undeploy(dep):
    db, engine, cfg = dep
    rid, run = await run_plan(db, engine)
    assert run["status"] == "done", run["error"]
    d = await finish(db, rid, ("ready", "failed"))
    assert d["status"] == "ready", d["error"]
    assert d["url"] == "http://127.0.0.1:%d" % cfg["port"]
    assert d["ready_at"] and d["log"] and d["name"]
    with urllib.request.urlopen(d["url"], timeout=5) as r:
        assert r.status == 200
    assert db.get_run(rid)["status"] == "done"
    ev = [json.loads(e["detail"]) for e in db.get_log("run:" + rid)
          if e["event"] == "deploy_install"]
    assert [e["cmd"] for e in ev] == cfg["install_commands"]
    assert ev[0].get("skipped") == "mock"
    assert db.list_deployments()[0]["run_id"] == rid
    out = await engine.undeploy(rid)
    assert out["status"] == "destroyed" and out["destroyed_at"]
    assert not port_open(cfg["port"])
    with pytest.raises(ValueError):
        await engine.undeploy(rid)


async def test_failing_install_marks_deployment_failed_run_stays_done(dep):
    db, engine, cfg = dep
    cfg["install_commands"] = ["echo boom >&2; false"]
    rid, run = await run_plan(db, engine)
    d = await finish(db, rid, ("ready", "failed"))
    assert d["status"] == "failed" and "boom" in d["error"]
    assert "install command" in d["error"] and d["log"]
    assert db.get_run(rid)["status"] == "done"
    failed = [e for e in db.get_log("run:" + rid) if e["event"] == "deploy_failed"]
    assert failed and "boom" in json.loads(failed[0]["detail"])["tail"]
    assert not port_open(cfg["port"])
    assert (await engine.undeploy(rid))["status"] == "destroyed"


async def test_target_none_no_deployment(dep):
    db, engine, cfg = dep
    cfg.update(target="none", install_commands=[], start_command="", port=0)
    rid, run = await run_plan(db, engine)
    assert run["status"] == "done"
    assert db.get_deployment(rid) is None
    with pytest.raises(ValueError, match="no VM deploy"):
        engine.deploy(rid)


async def test_manual_deploy(dep, monkeypatch):
    db, engine, cfg = dep
    monkeypatch.setattr(config, "DEPLOY_AUTO", False)
    rid, run = await run_plan(db, engine)
    assert db.get_deployment(rid) is None
    assert engine.deploy(rid)["status"] == "deploying"
    d = await finish(db, rid, ("ready", "failed"))
    assert d["status"] == "ready", d["error"]
    await wait_status(db, rid, ("done",))
    with pytest.raises(ValueError, match="undeploy it first"):
        engine.deploy(rid)
    await engine.undeploy(rid)
    assert not port_open(cfg["port"])


async def test_stop_while_deploying(dep, monkeypatch):
    db, engine, cfg = dep
    monkeypatch.setattr(config, "DEPLOY_HEALTH_TIMEOUT", 60)
    cfg.update(start_command="sleep 60", install_commands=[])
    rid, _ = await run_plan_no_wait(db, engine)
    await wait_status(db, rid, ("deploying",))
    await finish(db, rid, ("starting",))
    run = await engine.stop(rid)
    d = db.get_deployment(rid)
    assert d["status"] == "failed" and "stopped" in d["error"]
    assert db.get_run(rid)["status"] == "done"
    import os
    assert not os.path.exists(d["name"])      # mock VM dir gone, process killed


async def run_plan_no_wait(db, engine):
    from tests.test_plan import to_plan
    rid, _ = await to_plan(db, engine)
    engine.answer_plan(rid, True)
    return rid, None


def test_validate_plan_normalises_deploy(env):
    db, engine, llm, _ = env
    base = {"tasks": [{"id": "t1", "title": "x", "module": "m", "prompt": "p",
                       "tier": "deep", "depends_on": [], "acceptance": []}]}
    ok = engine._validate_plan("r", dict(base, deploy={
        "target": "vm", "start_command": "run", "port": "8080", "health_path": "h",
        "install_commands": [" a ", ""], "env": [{"name": "KEY", "value": None},
                                                 {"name": "bad name", "value": "x"}]}))
    assert ok["deploy"] == {"target": "vm", "install_commands": ["a"],
                            "start_command": "run", "port": 8080,
                            "health_path": "/h", "env": [{"name": "KEY", "value": ""}]}
    assert engine._validate_plan("r", dict(base))["deploy"]["target"] == "none"
    assert engine._validate_plan("r", dict(base, deploy={
        "target": "vm", "start_command": "x", "port": 0}))["deploy"]["target"] == "none"


def test_unit_text_and_app_name():
    d = {"start_command": "python3 -m http.server {port}", "port": 8080,
         "env": [{"name": "TOKEN", "value": ""}]}
    unit = systemd_unit("shop", d)
    assert "WorkingDirectory=/opt/shop\n" in unit
    assert "ExecStart=/bin/bash -lc 'python3 -m http.server 8080'\n" in unit
    assert "User=etl\n" in unit and "Restart=on-failure\n" in unit
    assert "EnvironmentFile=/opt/shop/.env\n" in unit
    assert "EnvironmentFile" not in systemd_unit("shop", dict(d, env=[]))
    assert app_name({"repo_layout": "shop/\n  api/"}, "r1") == "shop"
    assert app_name({"repo_layout": "src/\n  x"}, "r1") == "app-r1"
    assert app_name({"repo_layout": ""}, "r1") == "app-r1"
