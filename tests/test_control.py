"""Mid-run questions, leaf retry, tests:null gap, integration fix loop,
baseline .gitignore."""
import json
import os

import pytest

from pipez import config

from tests.test_code_leaf import (  # noqa: F401  (env is a fixture)
    PlanLLM, RecSandbox, env, git, run_plan, by_plan_id)
from tests.test_intake import _real_sleep, wait_status
from tests.test_plan import to_plan
from tests.test_stop import until


def log_events(db, task_id, name):
    return [json.loads(e["detail"]) for e in db.get_log(task_id) if e["event"] == name]


# -- mid-run question -------------------------------------------------------

async def test_ask_answer_rerun(env, monkeypatch):
    db, engine, llm, _ = env
    monkeypatch.setenv("PIPEZ_MOCK_RUNNER_ASK", "t2")
    monkeypatch.setattr(config, "SANDBOX_KEEP", True)
    rid, _ = await to_plan(db, engine)
    engine.answer_plan(rid, True)
    await wait_status(db, rid, ("awaiting_human", "failed", "done"))
    run = db.get_run(rid)
    assert run["status"] == "awaiting_human"
    ts = by_plan_id(db, rid)
    assert ts["t2"]["status"] == "awaiting_human"
    q = ts["t2"]["question"]
    assert q["key"] == "task.layout" and [o["value"] for o in q["options"]] == ["flat", "nested"]
    await until(lambda: by_plan_id(db, rid)["t1"]["status"] == "done")   # sibling proceeds
    assert by_plan_id(db, rid)["t3"]["status"] == "pending"              # dependent waits
    assert db.get_run(rid)["status"] == "awaiting_human"
    with pytest.raises(ValueError, match="task question"):
        engine.answer_intake(rid, accept_all=True)
    with pytest.raises(ValueError, match="not awaiting_human"):
        engine.answer_task(rid, ts["t1"]["id"], "x")
    with pytest.raises(ValueError, match="answer is required"):
        engine.answer_task(rid, ts["t2"]["id"], "  ")

    engine.answer_task(rid, ts["t2"]["id"], "nested", "keeps modules apart")
    assert db.get_run(rid)["status"] in ("running", "rendering", "done")
    run = await wait_status(db, rid, ("done", "failed"))
    assert run["status"] == "done", run["error"]
    ts = by_plan_id(db, rid)
    assert all(t["status"] == "done" for t in ts.values())
    obs = engine.prefs.observations("task.layout")
    assert len(obs) == 1 and obs[0]["value"] == "nested"
    assert obs[0]["scope"] == "request" and obs[0]["source"] == "intake"
    assert obs[0]["run_id"] == rid and obs[0]["reason"] == "keeps modules apart"
    assert run["answers"]["task.layout"] == "nested"
    md = open(os.path.join(db.get_sandbox(rid)["name"], "wt", ts["t2"]["id"],
                           ".pipez", "task.md")).read()
    assert "# Human answer" in md and "A: nested" in md
    assert "question.json" in md                     # contract text tells the agent how to ask
    assert [e["event"] for e in db.get_log(ts["t2"]["id"]) if e["event"] in ("question", "answered")] \
        == ["question", "answered"]


# -- retry ------------------------------------------------------------------

async def test_retry_second_attempt_succeeds(env, monkeypatch):
    db, engine, llm, _ = env
    monkeypatch.setattr(config, "LEAF_RETRIES", 1)
    monkeypatch.setenv("PIPEZ_MOCK_RUNNER_FLAKY", "t1")
    rid, run = await run_plan(db, engine)
    assert run["status"] == "done", run["error"]
    t1 = by_plan_id(db, rid)["t1"]
    assert t1["status"] == "done"
    retries = log_events(db, t1["id"], "leaf_retry")
    assert len(retries) == 1 and "1 failed (mock)" in retries[0]["tail"]
    assert len(log_events(db, t1["id"], "runner")) == 2
    assert t1["cost_usd"] == pytest.approx(0.02)         # cost accumulates
    assert len(log_events(db, t1["id"], "worktree")) == 2
    md = open(os.path.join(db.get_sandbox(rid)["name"], "wt", t1["id"], ".pipez", "task.md")).read()
    assert "# Previous attempt failed" in md and "1 failed (mock)" in md


async def test_retry_exhausted_fails(env, monkeypatch):
    db, engine, llm, _ = env
    monkeypatch.setattr(config, "LEAF_RETRIES", 1)
    monkeypatch.setenv("PIPEZ_MOCK_RUNNER_FAIL", "t2")
    rid, run = await run_plan(db, engine)
    t2 = by_plan_id(db, rid)["t2"]
    assert t2["status"] == "failed" and "1 failed (mock)" in t2["error"]
    assert len(log_events(db, t2["id"], "leaf_retry")) == 1
    assert len(log_events(db, t2["id"], "runner")) == 2
    assert by_plan_id(db, rid)["t3"]["status"] == "skipped"


async def test_tests_null_fails_when_required(env, monkeypatch):
    db, engine, llm, _ = env
    monkeypatch.setattr(config, "LEAF_RETRIES", 1)
    monkeypatch.setenv("PIPEZ_MOCK_RUNNER_NOTESTS", "t1")
    rid, run = await run_plan(db, engine)
    t1 = by_plan_id(db, rid)["t1"]
    assert t1["status"] == "failed" and "tests missing" in t1["error"]
    assert len(log_events(db, t1["id"], "leaf_retry")) == 1


async def test_tests_null_ok_when_not_required(env, monkeypatch):
    db, engine, llm, _ = env
    monkeypatch.setattr(config, "LEAF_REQUIRE_TESTS", False)
    monkeypatch.setenv("PIPEZ_MOCK_RUNNER_NOTESTS", "t1")
    rid, run = await run_plan(db, engine)
    assert by_plan_id(db, rid)["t1"]["status"] == "done"


# -- integration fix loop ---------------------------------------------------

def marker_plan(monkeypatch, cmd="test -f fixed.marker"):
    orig = PlanLLM.plan

    async def plan(self, system, prompt, tier=None):
        out, usage = await orig(self, system, prompt, tier)
        out["test_command"] = cmd
        return out, usage
    monkeypatch.setattr(PlanLLM, "plan", plan)


def root_events(db, rid, name):
    root = next(t for t in db.get_tasks(rid) if t["parent_id"] is None)
    return log_events(db, root["id"], name)


async def test_fix_loop_recovers(env, monkeypatch):
    db, engine, llm, _ = env
    monkeypatch.setattr(config, "FIX_ROUNDS", 2)
    monkeypatch.setenv("PIPEZ_MOCK_RUNNER_FIXMARK", "fixed.marker")
    marker_plan(monkeypatch)
    rid, run = await run_plan(db, engine)
    assert run["status"] == "done", run["error"]
    assert len(root_events(db, rid, "integrate_failed")) == 1
    assert len(root_events(db, rid, "integrate_ok")) == 1
    fixes = [t for t in db.get_tasks(rid) if t["module"] == "*"]
    assert len(fixes) == 1
    f = fixes[0]
    assert f["kind"] == "code" and f["role"] == "integrator-engineer"
    assert f["title"] == "Fix integration (round 1)" and f["status"] == "done"
    assert f["depends_on"] == [] and f["parent_id"] == next(
        t for t in db.get_tasks(rid) if t["parent_id"] is None)["id"]
    assert "test -f fixed.marker" in f["prompt"] and "do not alter public contracts" in f["prompt"]
    wd = db.get_sandbox(rid)["name"]
    assert "fixed.marker" in git(wd, "ls-tree", "-r", "--name-only", "main").splitlines()
    # no merge commit for the fixer: only the three leaf merges
    assert len(git(wd, "log", "--merges", "--oneline", "integration").splitlines()) == 3


async def test_fix_rounds_exhausted(env, monkeypatch):
    db, engine, llm, _ = env
    monkeypatch.setattr(config, "FIX_ROUNDS", 2)
    monkeypatch.setenv("PIPEZ_MOCK_RUNNER_FIXMARK", "wrong.marker")
    marker_plan(monkeypatch)
    rid, run = await run_plan(db, engine)
    assert run["status"] == "failed" and run["error"].startswith("integration failed")
    assert len(root_events(db, rid, "integrate_failed")) == 3
    assert len([t for t in db.get_tasks(rid) if t["module"] == "*"]) == 2
    assert not root_events(db, rid, "integrate_ok")
    assert db.get_sandbox(rid)["status"] == "kept"


async def test_fix_disabled_with_zero_rounds(env, monkeypatch):
    db, engine, llm, _ = env
    monkeypatch.setenv("PIPEZ_MOCK_RUNNER_FIXMARK", "fixed.marker")
    marker_plan(monkeypatch)
    rid, run = await run_plan(db, engine)           # env: FIX_ROUNDS = 0
    assert run["status"] == "failed"
    assert [t for t in db.get_tasks(rid) if t["module"] == "*"] == []


# -- baseline .gitignore ----------------------------------------------------

async def test_baseline_gitignore(env):
    db, engine, llm, _ = env
    rid, run = await run_plan(db, engine)
    assert run["status"] == "done", run["error"]
    wd = db.get_sandbox(rid)["name"]
    gi = git(wd, "show", "integration:.gitignore").splitlines()
    assert {"__pycache__/", "*.pyc", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/",
            "node_modules", ".venv", "dist/", "build/", ".pipez/"} <= set(gi)
    assert "scaffold" in git(wd, "log", "--format=%s", "--", ".gitignore")


async def test_scaffold_gitignore_kept(env):
    db, engine, llm, _ = env
    llm.scaffold = ["git init", "printf 'custom/\\n' > .gitignore"]
    rid, run = await run_plan(db, engine)
    assert run["status"] == "done", run["error"]
    wd = db.get_sandbox(rid)["name"]
    assert git(wd, "show", "integration:.gitignore") == "custom/"
