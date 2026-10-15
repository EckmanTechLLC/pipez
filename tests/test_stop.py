"""Stop a run mid-flight: DB consistent, sandbox kept, resume continues."""
import asyncio

import pytest

from pipez import config

from tests.test_code_leaf import (  # noqa: F401  (env is a fixture)
    PlanLLM, RecSandbox, env, git, run_plan, by_plan_id)
from tests.test_intake import _real_sleep, wait_status
from tests.test_plan import to_plan


async def until(pred, tries=3000):
    for _ in range(tries):
        if pred():
            return
        await _real_sleep(0.01)
    raise AssertionError("condition not reached")


async def test_stop_mid_leaf_then_resume(env, monkeypatch):
    db, engine, llm, _ = env
    monkeypatch.setenv("PIPEZ_MOCK_RUNNER_SLEEP", "2")
    rid, _ = await to_plan(db, engine)
    engine.answer_plan(rid, True)
    await until(lambda: any(t["status"] == "running" and t["kind"] == "code"
                            for t in db.get_tasks(rid)))
    run = await engine.stop(rid)
    assert run["status"] == "cancelled" and run["error"] == "stopped by user"
    tasks = db.get_tasks(rid)
    assert not [t for t in tasks if t["status"] in
                ("running", "pending", "awaiting_children", "synthesizing")]
    assert {t["status"] for t in tasks} == {"cancelled"}
    assert all(t["error"] == "stopped by user" for t in tasks)
    sb = db.get_sandbox(rid)
    assert sb["status"] == "kept" and sb["kept"] == 1
    assert any(e["event"] == "sandbox_kept" for e in db.get_log("run:" + rid))
    with pytest.raises(ValueError):          # terminal: nothing to stop
        await engine.stop(rid)

    monkeypatch.delenv("PIPEZ_MOCK_RUNNER_SLEEP")
    engine.resume(rid)
    run = await wait_status(db, rid, ("done", "failed"))
    assert run["status"] == "done", run["error"]
    assert all(t["status"] == "done" for t in db.get_tasks(rid))


async def test_stop_keeps_done_tasks_done(env, monkeypatch):
    db, engine, llm, _ = env
    monkeypatch.setenv("PIPEZ_MOCK_RUNNER_SLEEP", "0.3")
    rid, _ = await to_plan(db, engine)
    engine.answer_plan(rid, True)
    await until(lambda: any(t["status"] == "done" for t in db.get_tasks(rid)))
    await engine.stop(rid)
    st = {t["status"] for t in db.get_tasks(rid)}
    assert st <= {"done", "cancelled"} and "running" not in st


async def test_stop_before_tree_and_terminal_errors(env):
    db, engine, llm, _ = env
    rid, _ = await to_plan(db, engine)           # awaiting_plan, no tasks
    run = await engine.stop(rid)
    assert run["status"] == "cancelled"
    with pytest.raises(ValueError, match="no such run"):
        await engine.stop("nope")
    engine.resume(rid)                           # back to the plan gate
    assert db.get_run(rid)["status"] == "awaiting_plan"
