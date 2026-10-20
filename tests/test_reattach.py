"""Task 26: kept build sandboxes are re-attached after a server restart."""
import json
import shutil

import pytest

from pipez.engine import Engine
from pipez.events import EventBus
from pipez.prefs import PreferenceStore

from tests.test_code_leaf import RecSandbox, env, run_plan  # noqa: F401  (env is a fixture)
from tests.test_deploy import dep, finish  # noqa: F401  (dep is a fixture)
from tests.test_deploy_failure import flaky, settle  # noqa: F401  (flaky is a fixture)
from tests.test_intake import _real_sleep


def events(db, rid, name):
    return [json.loads(e["detail"]) for e in db.get_log("run:" + rid) if e["event"] == name]


def restarted(db, llm):
    return Engine(db, EventBus(), llm, PreferenceStore(db), sandbox_factory=RecSandbox)


async def kept_run(flaky):
    """A done run with a kept sandbox and a ready deployment; engine has no handle."""
    db, engine, cfg, marker = flaky
    rid, run = await run_plan(db, engine)
    marker.write_text("1")
    engine.deploy(rid)
    await settle(db, rid)
    assert db.get_deployment(rid)["status"] == "ready"
    assert db.get_sandbox(rid)["status"] == "kept"
    engine._sandboxes.pop(rid, None)
    return rid


async def test_recover_attaches_kept_sandbox(flaky):
    db, engine, cfg, marker = flaky
    rid = await kept_run(flaky)
    engine2 = restarted(db, engine.llm)
    out = await engine2.recover()
    assert {"run_id": rid, "attached": True} in out["sandboxes"]
    assert rid in engine2._sandboxes
    assert events(db, rid, "sandbox_attached")
    assert db.get_sandbox(rid)["status"] == "kept"
    await engine.undeploy(rid)


async def test_recover_marks_dead_kept_sandbox_destroyed(flaky):
    db, engine, cfg, marker = flaky
    rid = await kept_run(flaky)
    shutil.rmtree(db.get_sandbox(rid)["name"], ignore_errors=True)
    engine2 = restarted(db, engine.llm)
    out = await engine2.recover()
    assert {"run_id": rid, "attached": False} in out["sandboxes"]
    assert rid not in engine2._sandboxes
    row = db.get_sandbox(rid)
    assert row["status"] == "destroyed" and row["error"] == "kept sandbox is gone"
    assert events(db, rid, "sandbox_gone")
    await engine.undeploy(rid)


async def test_reverify_after_reattach(flaky):
    db, engine, cfg, marker = flaky
    rid = await kept_run(flaky)
    engine2 = restarted(db, engine.llm)
    assert rid not in engine2._sandboxes
    assert (await engine2.reverify(rid))["status"] == "verifying"
    assert events(db, rid, "sandbox_attached")
    for _ in range(3000):
        if db.get_run(rid)["status"] == "done":
            break
        await _real_sleep(0.01)
    assert db.get_run(rid)["status"] == "done"
    assert db.get_run(rid)["verification"] == "passed"
    await engine.undeploy(rid)


async def test_destroy_detached_kept_sandbox(flaky):
    db, engine, cfg, marker = flaky
    rid = await kept_run(flaky)
    engine2 = restarted(db, engine.llm)
    row = await engine2.destroy_sandbox(rid)
    assert row["status"] == "destroyed"
    assert events(db, rid, "sandbox_attached")
    await engine.undeploy(rid)
