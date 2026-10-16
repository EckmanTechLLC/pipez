"""Task 15: lazy server state, restart recovery, remote runner kill, orphan sweep."""
import asyncio
import glob
import os
import subprocess
import sys

import pytest

from pipez import config
from pipez.engine import Engine
from pipez.events import EventBus
from pipez.prefs import PreferenceStore

from tests.test_code_leaf import (  # noqa: F401  (env is a fixture)
    RecSandbox, env, run_plan)
from tests.test_deploy import dep, finish, run_plan_no_wait  # noqa: F401
from tests.test_intake import _real_sleep, wait_status
from tests.test_plan import to_plan
from tests.test_stop import until

ROOT = os.path.join(os.path.dirname(__file__), "..")


def test_import_server_touches_no_files(tmp_path):
    subprocess.run([sys.executable, "-c", "import pipez.server"], cwd=str(tmp_path),
                   env=dict(os.environ, PYTHONPATH=os.path.abspath(ROOT)),
                   check=True, capture_output=True, timeout=60)
    assert os.listdir(str(tmp_path)) == []


async def crash(engine, monkeypatch):
    """Simulate the server dying: drivers vanish without any cleanup."""
    async def noop(*a, **k):
        return None
    monkeypatch.setattr(engine, "_mark_cancelled", lambda run_id: None)
    monkeypatch.setattr(engine, "_release_sandbox", noop)
    monkeypatch.setattr(engine, "_abort_deploy", noop)
    tasks = list(engine._runs)
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def restarted(db, llm):
    return Engine(db, EventBus(), llm, PreferenceStore(db), sandbox_factory=RecSandbox)


async def test_recover_running_run_then_resume(env, monkeypatch):
    db, engine, llm, _ = env
    monkeypatch.setenv("PIPEZ_MOCK_RUNNER_SLEEP", "2")
    rid, _ = await to_plan(db, engine)
    engine.answer_plan(rid, True)
    await until(lambda: any(t["status"] == "running" and t["kind"] == "code"
                            for t in db.get_tasks(rid)))
    await crash(engine, monkeypatch)
    assert db.get_run(rid)["status"] == "running"      # stuck, as after a real crash

    engine2 = restarted(db, llm)
    out = await engine2.recover()
    assert out["runs"] == [rid]
    run = db.get_run(rid)
    assert run["status"] == "failed" and run["error"] == "interrupted by server restart"
    assert {t["status"] for t in db.get_tasks(rid)} == {"cancelled"}
    assert all(t["error"] == "interrupted by server restart" for t in db.get_tasks(rid))
    assert any(e["event"] == "interrupted" and '"running"' in e["detail"]
               for e in db.get_log("run:" + rid))
    sb = db.get_sandbox(rid)
    assert sb["status"] == "kept" and sb["kept"] == 1
    assert await engine2.recover() == {"runs": [], "sandboxes": [], "deployments": []}

    monkeypatch.delenv("PIPEZ_MOCK_RUNNER_SLEEP")
    engine2.resume(rid)                 # human resumes; never automatic
    run = await wait_status(db, rid, ("done", "failed"))
    assert run["status"] == "done", run["error"]
    assert all(t["status"] == "done" for t in db.get_tasks(rid))


async def test_recover_planning_run_resumes_through_plan(env):
    db, engine, llm, _ = env
    rid, _ = await to_plan(db, engine)
    db.set_run_status(rid, "planning")
    engine2 = restarted(db, llm)
    await engine2.recover()
    assert db.get_run(rid)["status"] == "failed"
    engine2.resume(rid)
    assert (await wait_status(db, rid, ("awaiting_plan", "failed")))["status"] == "awaiting_plan"


async def test_recover_leaves_waiting_and_terminal_runs(env):
    db, engine, llm, _ = env
    rid, _ = await to_plan(db, engine)                  # awaiting_plan
    done = db.create_run("x")
    db.finish_run(done, "done", result={"output": "o"})
    assert (await restarted(db, llm).recover())["runs"] == []
    assert db.get_run(rid)["status"] == "awaiting_plan" and db.get_run(done)["status"] == "done"


async def test_recover_deploying_tears_down_vm(dep, monkeypatch):
    db, engine, cfg = dep
    monkeypatch.setattr(config, "DEPLOY_HEALTH_TIMEOUT", 60)
    cfg.update(start_command="sleep 60", install_commands=[])
    rid, _ = await run_plan_no_wait(db, engine)
    await wait_status(db, rid, ("deploying",))
    d = await finish(db, rid, ("starting",))
    await crash(engine, monkeypatch)
    db.set_run_status(rid, "deploying")
    assert os.path.isdir(d["name"])

    llm = engine.llm
    out = await restarted(db, llm).recover()
    assert rid in out["deployments"] and rid in out["runs"]
    d = db.get_deployment(rid)
    assert d["status"] == "failed" and d["error"] == "interrupted by server restart"
    assert not os.path.exists(d["name"])                # VM torn down
    assert db.get_run(rid)["status"] == "failed"


async def test_stop_kills_remote_runner_group(env, monkeypatch):
    db, engine, llm, _ = env
    monkeypatch.setenv("PIPEZ_MOCK_RUNNER_SLEEP", "5")
    rid, _ = await to_plan(db, engine)
    engine.answer_plan(rid, True)
    await until(lambda: engine._sandboxes.get(rid) is not None and glob.glob(
        os.path.join(engine._sandboxes[rid].workdir, "run", "*.pid")))
    sb = engine._sandboxes[rid]
    with open(glob.glob(os.path.join(sb.workdir, "run", "*.pid"))[0]) as f:
        pgid = int(f.read())
    for _ in range(40):         # the child may not have called setsid() yet
        try:
            os.killpg(pgid, 0)
            break
        except ProcessLookupError:
            await _real_sleep(0.05)
    else:
        raise AssertionError("runner process group %s never came alive" % pgid)
    await engine.stop(rid)
    assert pgid in sb.killed
    for _ in range(100):
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            break
        await _real_sleep(0.05)
    else:
        raise AssertionError("runner process group survived the stop")
    assert glob.glob(os.path.join(sb.workdir, "run", "*.pid")) == []


class FakePve:
    def __init__(self, members):
        self.members, self.stopped, self.destroyed = list(members), [], []

    async def pool_members(self, pool):
        return list(self.members)

    async def stop(self, vmid):
        self.stopped.append(vmid)
        return "UPID:stop"

    async def destroy(self, vmid):
        self.destroyed.append(vmid)
        return "UPID:destroy"

    async def wait_task(self, upid, **kw):
        return {}

    async def aclose(self):
        pass


def sweep_setup(db, llm, fake):
    for rid, vmid, status in (("ra", 9101, "kept"), ("rb", 9199, "kept")):
        db.create_run(rid)
        db.upsert_sandbox(rid, kind="pve", vmid=vmid, name="pipez-" + rid, status=status)
    db.create_run("rd")
    db.upsert_deployment("rd", vmid=9201, name="pipez-app-rd", status="ready")
    return Engine(db, EventBus(), llm, PreferenceStore(db), pve_factory=lambda: fake)


async def test_sweep_reports_orphans_and_marks_gone_rows(env):
    db, _, llm, _ = env
    fake = FakePve([9000, 9001, 9090, 9101, 9150, 9201, 9299])
    engine = sweep_setup(db, llm, fake)
    report = await engine.sweep()
    assert sorted((r["vmid"], r["action"]) for r in report) == [
        (9150, "reported"), (9199, "row_destroyed"), (9299, "reported")]
    assert fake.stopped == [] and fake.destroyed == []
    assert db.get_sandbox("rb")["status"] == "destroyed"
    assert db.get_sandbox("ra")["status"] == "kept"
    assert db.get_deployment("rd")["status"] == "ready"


async def test_sweep_destroys_only_in_range_orphans(env, monkeypatch):
    db, _, llm, _ = env
    monkeypatch.setattr(config, "SWEEP_DESTROY", True)
    fake = FakePve([9000, 9001, 9090, 9101, 9150, 9201])
    engine = sweep_setup(db, llm, fake)
    report = await engine.sweep()
    assert fake.destroyed == [9150] and fake.stopped == [9150]
    assert {"vmid": 9150, "name": None, "action": "destroyed"} in report
    assert not {9000, 9001, 9090} & set(fake.destroyed + fake.stopped)


async def test_sweep_is_noop_for_mock_config(env):
    db, engine, llm, _ = env
    assert await engine.sweep() == []
