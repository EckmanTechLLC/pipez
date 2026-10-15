"""Coding leaves: mock sandbox + mock runner + real git in a temp dir."""
import asyncio
import json
import os
import shutil
import subprocess

import pytest

from pipez import config
from pipez.db import Database
from pipez.engine import Engine
from pipez.events import EventBus
from pipez.llm import MockLLM
from pipez.prefs import PreferenceStore
from pipez import seed_prompts
from pipez.sandbox import MockSandbox

from tests.test_intake import _fast_sleep, _real_sleep, wait_status
from tests.test_plan import to_plan

KEY = "sk-ant-TESTSECRET-0123456789"


class PlanLLM(MockLLM):
    """t3 depends on t2 (not t1), so a failing t2 skips t3 and spares t1."""
    scaffold = None

    async def plan(self, system, prompt, tier=None):
        out, usage = await super().plan(system, prompt, tier)
        out["tasks"][2]["depends_on"] = ["t2"]
        if self.scaffold is not None:
            out["scaffold_commands"] = self.scaffold
        return out, usage


class RecSandbox(MockSandbox):
    """Records every composed command line."""
    lines: list = []

    async def _exec(self, line, stdin, timeout):
        RecSandbox.lines.append(line)
        return await super()._exec(line, stdin, timeout)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_ARTIFACTS", False)
    monkeypatch.setattr(config, "SANDBOX_KEEP", True)
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", KEY)
    monkeypatch.setattr(config, "FIX_ROUNDS", 0)       # fix loop has its own tests
    monkeypatch.setattr(config, "LEAF_RETRIES", 0)
    monkeypatch.delenv("PIPEZ_MOCK_RUNNER_ASK", raising=False)
    monkeypatch.delenv("PIPEZ_MOCK_RUNNER_FLAKY", raising=False)
    monkeypatch.delenv("PIPEZ_MOCK_RUNNER_FIXMARK", raising=False)
    monkeypatch.delenv("PIPEZ_MOCK_RUNNER_SLEEP", raising=False)
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
    monkeypatch.delenv("PIPEZ_MOCK_RUNNER_FAIL", raising=False)
    monkeypatch.delenv("PIPEZ_MOCK_RUNNER_CONFLICT", raising=False)
    RecSandbox.lines = []
    db = Database(str(tmp_path / "c.sqlite3"))
    seed_prompts.seed(db)
    llm = PlanLLM()
    engine = Engine(db, EventBus(), llm, PreferenceStore(db), sandbox_factory=RecSandbox)
    made = []
    yield db, engine, llm, made
    for r in db.list_sandboxes():
        if r.get("name") and r["kind"] == "mock":
            shutil.rmtree(r["name"], ignore_errors=True)


def git(wd, *args):
    p = subprocess.run(["git", "-C", os.path.join(wd, "repo")] + list(args),
                       capture_output=True, text=True)
    return p.stdout.strip()


async def run_plan(db, engine, rid=None):
    if rid is None:
        rid, _ = await to_plan(db, engine)
        engine.answer_plan(rid, True)
    run = await wait_status(db, rid, ("done", "failed"))
    for _ in range(300):    # sandbox released after the run row finishes
        if db.get_sandbox(rid)["status"] in ("destroyed", "kept"):
            break
        await _real_sleep(0.01)
    return rid, run


def by_plan_id(db, rid):
    ts = sorted([t for t in db.get_tasks(rid) if t["kind"] == "code"], key=lambda t: t["seq"])
    return {"t%d" % (i + 1): t for i, t in enumerate(ts)}


async def test_happy_path(env):
    db, engine, llm, _ = env
    rid, run = await run_plan(db, engine)
    assert run["status"] == "done", run["error"]
    wd = db.get_sandbox(rid)["name"]
    ts = by_plan_id(db, rid)
    assert all(t["status"] == "done" for t in ts.values())
    res = json.loads(ts["t1"]["result"])
    assert res["metadata"]["files_changed"] and res["metadata"]["tests"]["passed"] == 1
    assert res["facts"][0]["source"] == "core/t1.py"
    assert ts["t1"]["cost_usd"] == pytest.approx(0.01) and ts["t1"]["output_tokens"] == 5
    merges = git(wd, "log", "--merges", "--oneline", "integration").splitlines()
    assert len(merges) == 3
    tree = git(wd, "ls-tree", "-r", "--name-only", "integration").splitlines()
    assert {"core/t1.py", "core/t2.py", "api/t3.py"} <= set(tree)
    assert git(wd, "status", "--porcelain") == ""
    # the dependent's worktree branched off integration: it has its dependency's file
    assert os.path.exists(os.path.join(wd, "wt", ts["t3"]["id"], "core", "t2.py"))
    task_md = open(os.path.join(wd, "wt", ts["t3"]["id"], ".pipez", "task.md")).read()
    assert "Plan task id: t3" in task_md and "CONTRACT-API" in task_md
    assert "- [ ] endpoint returns 200" in task_md and "mock built t2" in task_md
    # scaffold rows logged
    sc = [e for e in db.get_log("run:" + rid) if e["event"] == "scaffold"]
    assert [json.loads(e["detail"])["cmd"] for e in sc] == ["git init", "mkdir -p tests"]


async def test_secrets_never_in_commands(env):
    db, engine, llm, _ = env
    rid, run = await run_plan(db, engine)
    assert run["status"] == "done", run["error"]
    assert RecSandbox.lines and not any(KEY in l for l in RecSandbox.lines)
    assert any("pipez_runner" in l or "runner.py" in l for l in RecSandbox.lines)
    envfile = os.path.join(db.get_sandbox(rid)["name"], ".env")
    assert KEY in open(envfile).read() and os.stat(envfile).st_mode & 0o777 == 0o600
    assert any(". " in l and ".env" in l for l in RecSandbox.lines)   # sourced, not inlined


async def test_failing_tests_skip_dependents(env, monkeypatch):
    db, engine, llm, _ = env
    monkeypatch.setenv("PIPEZ_MOCK_RUNNER_FAIL", "t2")
    rid, run = await run_plan(db, engine)
    ts = by_plan_id(db, rid)
    assert ts["t2"]["status"] == "failed" and "1 failed (mock)" in ts["t2"]["error"]
    assert ts["t3"]["status"] == "skipped"
    assert ts["t1"]["status"] == "done"
    wd = db.get_sandbox(rid)["name"]
    assert len(git(wd, "log", "--merges", "--oneline", "integration").splitlines()) == 1
    assert db.get_sandbox(rid)["status"] == "kept"


async def test_merge_conflict(env, monkeypatch):
    db, engine, llm, _ = env
    monkeypatch.setenv("PIPEZ_MOCK_RUNNER_CONFLICT", "1")
    rid, run = await run_plan(db, engine)
    ts = by_plan_id(db, rid)
    failed = [t for t in ts.values() if t["status"] == "failed"]
    assert failed and all("merge_conflict" in t["error"] and "CONFLICT.md" in t["error"]
                          for t in failed)
    assert any(t["status"] == "done" for t in ts.values())
    assert any(e["event"] == "merge_conflict" for t in failed for e in db.get_log(t["id"]))
    wd = db.get_sandbox(rid)["name"]
    assert git(wd, "status", "--porcelain") == ""
    assert not os.path.exists(os.path.join(wd, "repo", ".git", "MERGE_HEAD"))


async def test_resume_reruns_only_failed_and_dependents(env, monkeypatch):
    db, engine, llm, _ = env
    monkeypatch.setenv("PIPEZ_MOCK_RUNNER_FAIL", "t2")
    rid, run = await run_plan(db, engine)
    ts = by_plan_id(db, rid)
    assert ts["t2"]["status"] == "failed" and ts["t3"]["status"] == "skipped"
    monkeypatch.delenv("PIPEZ_MOCK_RUNNER_FAIL")
    engine.resume(rid)
    await _real_sleep(0.05)
    rid, run = await run_plan(db, engine, rid)
    assert run["status"] == "done", run["error"]
    ts = by_plan_id(db, rid)
    assert all(t["status"] == "done" for t in ts.values())
    n = lambda t: sum(1 for e in db.get_log(t["id"]) if e["event"] == "worktree")
    assert (n(ts["t1"]), n(ts["t2"]), n(ts["t3"])) == (1, 2, 1)
    wd = db.get_sandbox(rid)["name"]
    assert len(git(wd, "log", "--merges", "--oneline", "integration").splitlines()) == 3
    assert any(e["event"] == "workspace_reused" for e in db.get_log("run:" + rid))


async def test_scaffold_failure_fails_run_before_leaves(env):
    db, engine, llm, _ = env
    llm.scaffold = ["echo hi", "false", "echo never"]
    rid, run = await run_plan(db, engine)
    assert run["status"] == "failed" and run["error"].startswith("scaffold_failed")
    sc = [e for e in db.get_log("run:" + rid) if e["event"] == "scaffold"]
    assert len(sc) == 2
    ts = db.get_tasks(rid)
    assert all(t["status"] == "pending" for t in ts if t["kind"] == "code")
    assert not any(e["event"] == "worktree" for t in ts for e in db.get_log(t["id"]))



# -- task 12: authoritative test command, venv in worktrees, failed leaves fail the run --

def test_runner_never_executes_a_placeholder_claim(tmp_path):
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "infra", "runner"))
    import pipez_runner
    marker = tmp_path / "ran"
    for claim in ("none", "n/a", "-", "", "no tests", "definitely-not-a-binary"):
        assert pipez_runner.run_tests(str(tmp_path), {"command": claim}) is None
    assert pipez_runner.run_tests(str(tmp_path), None) is None
    assert pipez_runner.run_tests(str(tmp_path), {"command": "touch %s" % marker}) is not None
    assert marker.exists()      # a plausible claim without a project command still runs


def test_runner_project_command_is_authoritative(tmp_path):
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "infra", "runner"))
    import pipez_runner
    marker = tmp_path / "claimed_ran"
    res = pipez_runner.run_tests(
        str(tmp_path), {"command": "touch %s" % marker, "passed": 9, "failed": 0},
        "echo '3 passed, 1 failed in 0.1s'; exit 1")
    assert not marker.exists()
    assert res["command"].startswith("echo '3 passed")
    assert res["claimed"] == "touch %s" % marker
    assert (res["passed"], res["failed"], res["rc"]) == (3, 1, 1)
    ok = pipez_runner.run_tests(str(tmp_path), {"command": "none"}, "true")
    assert ok["rc"] == 0 and ok["failed"] == 0 and ok["claimed"] == "none"


async def test_engine_passes_project_command_and_records_claim(env):
    db, engine, llm, _ = env
    rid, run = await run_plan(db, engine)
    assert run["status"] == "done", run["error"]
    assert any("--test-command" in l and "pytest" in l for l in RecSandbox.lines)
    tests = json.loads(by_plan_id(db, rid)["t1"]["result"])["metadata"]["tests"]
    assert tests["command"] == "python3 -m pytest -q" and tests["claimed"]
    wd = db.get_sandbox(rid)["name"]
    md = open(os.path.join(wd, "wt", by_plan_id(db, rid)["t1"]["id"], ".pipez", "task.md")).read()
    assert "## Environment" in md and ".venv/bin/python" in md


async def test_worktree_gets_venv_symlink(env):
    db, engine, llm, _ = env
    llm.scaffold = ["git init", "mkdir -p .venv"]
    rid, run = await run_plan(db, engine)
    assert run["status"] == "done", run["error"]
    wd = db.get_sandbox(rid)["name"]
    for t in by_plan_id(db, rid).values():
        link = os.path.join(wd, "wt", t["id"], ".venv")
        assert os.path.islink(link)
        assert os.path.realpath(link) == os.path.realpath(os.path.join(wd, "repo", ".venv"))


async def test_exclude_has_bare_venv_and_reused_appends_once(env, monkeypatch):
    db, engine, llm, _ = env
    monkeypatch.setattr(config, "SANDBOX_KEEP", True)
    rid, run = await run_plan(db, engine)
    assert run["status"] == "done", run["error"]
    wd = db.get_sandbox(rid)["name"]
    exc = os.path.join(wd, "repo", ".git", "info", "exclude")
    lines = open(exc).read().splitlines()
    assert ".venv" in lines and "node_modules" in lines
    # reused workspace: strip the lines, re-init twice, they come back exactly once
    open(exc, "w").write("\n".join(l for l in lines if l not in (".venv", "node_modules")) + "\n")
    sb = engine._sandbox_factory(rid)
    sb.adopt(db.get_sandbox(rid))
    engine._sandboxes[rid] = sb
    for _ in range(2):
        await engine._init_workspace(rid)
    lines = open(exc).read().splitlines()
    assert lines.count(".venv") == 1 and lines.count("node_modules") == 1


async def test_failed_leaf_fails_run_and_resume_integrates(env, monkeypatch):
    db, engine, llm, _ = env
    monkeypatch.setenv("PIPEZ_MOCK_RUNNER_FAIL", "t2")
    rid, run = await run_plan(db, engine)
    assert run["status"] == "failed"
    assert run["error"].startswith("2 code tasks failed:")
    assert db.get_sandbox(rid)["status"] == "kept"
    root = next(t for t in db.get_tasks(rid) if t["parent_id"] is None)
    assert not any(e["event"] == "resume_resynthesis" for e in db.get_log(root["id"]))
    assert db.get_artifacts(rid) == []
    ts = by_plan_id(db, rid)
    n = lambda t: sum(1 for e in db.get_log(t["id"]) if e["event"] == "worktree")
    # resume: only the failed leaf and its skipped dependent run again, then integrate
    monkeypatch.delenv("PIPEZ_MOCK_RUNNER_FAIL")
    engine.resume(rid)
    await _real_sleep(0.05)
    rid, run = await run_plan(db, engine, rid)
    assert run["status"] == "done", run["error"]
    ts = by_plan_id(db, rid)
    assert (n(ts["t1"]), n(ts["t2"]), n(ts["t3"])) == (1, 2, 1)
    names = [e["event"] for e in db.get_log(root["id"])]
    assert "integrate_ok" in names and "resume_resynthesis" in names
