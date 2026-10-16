"""Acceptance review stage (task 13a): mock sandbox + MockDeploySandbox + mock runner."""
import importlib.util
import io
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from pipez import config
from pipez.engine import REVIEW_ROLE

from tests.test_code_leaf import env, run_plan  # noqa: F401  (env is a fixture)
from tests.test_deploy import dep, finish, port_open  # noqa: F401  (dep is a fixture)
from tests.test_intake import _real_sleep, wait_status
from tests.test_plan import to_plan

RUNNER = os.path.join(os.path.dirname(__file__), "..", "infra", "runner", "pipez_runner.py")


def events(db, rid, name):
    return [json.loads(e["detail"]) for e in db.get_log("run:" + rid) if e["event"] == name]


async def test_auto_review_passes(dep):
    db, engine, cfg = dep
    rid, run = await run_plan(db, engine)
    assert run["status"] == "done", run["error"]
    run = db.get_run(rid)
    assert run["verification"] == "passed"
    assert db.get_sandbox(rid)["status"] == "destroyed"
    assert db.get_deployment(rid)["status"] == "ready"
    vs = db.get_verifications(rid)
    assert len(vs) == 1 and len(json.loads(vs[0]["verdicts"])) == 2
    assert {json.loads(vs[0]["verdicts"])[0]["verdict"]} == {"confirmed"}
    rev = [t for t in db.get_tasks(rid) if t["role"] == REVIEW_ROLE]
    assert len(rev) == 1 and rev[0]["status"] == "done" and rev[0]["tier"] == config.VERIFY_TIER
    assert rev[0]["module"] == "*" and rev[0]["kind"] == "code"
    assert events(db, rid, "review_done")[0]["verification"] == "passed"
    # the sandbox is gone, so a manual re-verify is refused
    with pytest.raises(ValueError, match="sandbox"):
        engine.reverify(rid)
    await engine.undeploy(rid)


async def test_review_prompt_lists_every_criterion(dep):
    db, engine, cfg = dep
    seen = []
    orig = engine._review_md

    def spy(task, run, failure=None):
        md = orig(task, run, failure)
        seen.append(md)
        return md
    engine._review_md = spy
    rid, run = await run_plan(db, engine)
    n = sum(len(t.get("acceptance") or []) for t in db.get_run(rid)["plan"]["tasks"])
    md = seen[0]
    assert n > 0 and ("%d. [" % n) in md and ("%d. [" % (n + 1)) not in md
    assert "http://127.0.0.1:%d" % cfg["port"] in md and "`/`" in md
    assert "Read-only" in md or "read-only" in md.lower()
    await engine.undeploy(rid)


async def test_wrong_verdict_fails_flag_keeps_sandbox(dep, monkeypatch):
    db, engine, cfg = dep
    monkeypatch.setattr(config, "VERIFY_ROUNDS", 0)
    orig = engine.llm.plan

    async def plan(system, prompt, tier=None):
        out, usage = await orig(system, prompt, tier)
        out["tasks"][0]["acceptance"] = list(out["tasks"][0].get("acceptance") or []) + ["MOCK_WRONG"]
        return out, usage
    engine.llm.plan = plan
    rid, run = await run_plan(db, engine)
    assert run["status"] == "done", run["error"]
    assert db.get_run(rid)["verification"] == "failed"
    assert db.get_sandbox(rid)["status"] == "kept"
    assert db.get_deployment(rid)["status"] == "ready"
    verdicts = json.loads(db.get_verifications(rid)[0]["verdicts"])
    assert [v["verdict"] for v in verdicts] == ["wrong"] and verdicts[0]["correction"]
    # manual re-verify runs the review again on the kept sandbox
    assert engine.reverify(rid)["status"] == "verifying"
    for _ in range(3000):
        if db.get_run(rid)["status"] == "done":
            break
        await _real_sleep(0.01)
    assert db.get_run(rid)["status"] == "done" and db.get_run(rid)["verification"] == "failed"
    assert len(db.get_verifications(rid)) == 1       # replaced, not appended
    assert db.get_sandbox(rid)["status"] == "kept"
    await engine.undeploy(rid)


async def test_review_skipped_without_deploy(env):
    db, engine, llm, _ = env          # DEPLOY_AUTO is off (conftest)
    rid, run = await run_plan(db, engine)
    assert run["status"] == "done", run["error"]
    assert db.get_run(rid)["verification"] == "skipped"
    assert db.get_verifications(rid) == []
    assert "deployment is absent" in events(db, rid, "review_skipped")[0]["reason"]
    assert not [t for t in db.get_tasks(rid) if t["role"] == REVIEW_ROLE]
    assert db.get_sandbox(rid)["status"] == "destroyed"


async def test_stop_during_verifying(dep, monkeypatch):
    db, engine, cfg = dep
    orig = engine._review

    async def slow(run_id, root=None, rnd=1):
        monkeypatch.setenv("PIPEZ_MOCK_RUNNER_SLEEP", "5")
        return await orig(run_id, root, rnd)
    engine._review = slow
    rid, _ = await to_plan(db, engine)
    engine.answer_plan(rid, True)
    await wait_status(db, rid, ("verifying",))
    for _ in range(3000):
        if any(t["role"] == REVIEW_ROLE and t["status"] == "running" for t in db.get_tasks(rid)):
            break
        await _real_sleep(0.01)
    run = await engine.stop(rid)
    assert run["status"] == "cancelled" and run["error"] == "stopped by user"
    rev = [t for t in db.get_tasks(rid) if t["role"] == REVIEW_ROLE]
    assert [t["status"] for t in rev] == ["cancelled"]
    assert db.get_sandbox(rid)["status"] == "kept"
    assert db.get_deployment(rid)["status"] == "ready"
    assert db.get_run(rid)["verification"] is None
    await engine.undeploy(rid)
    assert not port_open(cfg["port"])


def load_runner():
    spec = importlib.util.spec_from_file_location("pipez_runner_under_test", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_review(tmp_path, monkeypatch, mutate):
    wt = tmp_path / "wt"
    wt.mkdir()
    for cmd in (["git", "init", "-q", "-b", "integration"],
                ["git", "config", "user.email", "t@t"], ["git", "config", "user.name", "t"]):
        subprocess.run(cmd, cwd=wt, check=True)
    (wt / "app.py").write_text("x = 1\n")
    (wt / ".gitignore").write_text(".pipez/\n__pycache__/\n")
    subprocess.run("git add -A && git commit -q -m init", cwd=wt, shell=True, check=True)
    (wt / ".pipez").mkdir()
    (wt / ".pipez" / "task.md").write_text("review")
    (wt / ".pipez" / "verdicts.json").write_text(json.dumps({
        "summary": "s", "ok": True,
        "verdicts": [{"claim": "c", "verdict": "confirmed", "note": "n", "correction": ""}]}))
    mutate(wt)
    runner = load_runner()

    async def fake_session(args, prompt, log):
        return SimpleNamespace(is_error=False, usage={}, total_cost_usd=0.2, num_turns=3), ""
    monkeypatch.setattr(runner, "run_session", fake_session)
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)       # main() rebinds sys.stdout; monkeypatch restores
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    monkeypatch.setattr(sys, "argv", [
        "runner", "--cwd", str(wt), "--task-file", ".pipez/task.md", "--model", "m",
        "--log", str(tmp_path / "log.jsonl"), "--review", "--test-command", "false"])
    runner.main()
    return json.loads(buf.getvalue().strip().splitlines()[-1])


def test_runner_review_clean_tree(tmp_path, monkeypatch):
    out = run_review(tmp_path, monkeypatch, lambda wt: None)
    assert out["ok"] is True and out["error"] is None
    assert out["commit"] is None and out["tests"] is None      # no commit, no run_tests
    assert out["verdicts"]["verdicts"][0]["verdict"] == "confirmed"
    assert out["summary"] == "s"


def test_runner_review_refuses_dirty_tree(tmp_path, monkeypatch):
    out = run_review(tmp_path, monkeypatch, lambda wt: (wt / "stray.txt").write_text("x"))
    assert out["ok"] is False and "reviewer modified the worktree" in out["error"]


def test_runner_review_refuses_tracked_change(tmp_path, monkeypatch):
    out = run_review(tmp_path, monkeypatch, lambda wt: (wt / "app.py").write_text("x = 2\n"))
    assert out["ok"] is False and "reviewer modified the worktree" in out["error"]


def test_runner_review_missing_verdicts(tmp_path, monkeypatch):
    out = run_review(tmp_path, monkeypatch,
                     lambda wt: (wt / ".pipez" / "verdicts.json").unlink())
    assert out["ok"] is False and out["verdicts"] is None and out["error"] == "no verdicts.json"


# -- fix rounds (task 13b) ---------------------------------------------------

def wrong_plan(engine):
    orig = engine.llm.plan

    async def plan(system, prompt, tier=None):
        out, usage = await orig(system, prompt, tier)
        out["tasks"][0]["acceptance"] = list(out["tasks"][0].get("acceptance") or []) + ["MOCK_WRONG"]
        return out, usage
    engine.llm.plan = plan


def wrong_once(engine):
    """The reviewer's task file loses MOCK_WRONG from the second review on,
    as if the fixer had resolved the finding."""
    orig = engine._review_md
    calls = []

    def spy(task, run, failure=None):
        md = orig(task, run, failure)
        calls.append(1)
        return md if len(calls) == 1 else md.replace("MOCK_WRONG", "fine")
    engine._review_md = spy


def fixers(db, rid):
    return [t for t in db.get_tasks(rid) if t["title"].startswith("Fix review findings")]


async def test_fix_round_passes(dep, monkeypatch):
    db, engine, cfg = dep
    monkeypatch.setattr(config, "VERIFY_ROUNDS", 1)
    wrong_plan(engine)
    wrong_once(engine)
    rid, run = await run_plan(db, engine)
    assert run["status"] == "done", run["error"]
    run = db.get_run(rid)
    assert run["verification"] == "passed"
    assert db.get_sandbox(rid)["status"] == "destroyed"
    vs = db.get_verifications(rid)
    assert [v["round"] for v in vs] == [1, 2]
    assert [json.loads(v["verdicts"])[0]["verdict"] for v in vs] == ["wrong", "confirmed"]
    fx = fixers(db, rid)
    assert len(fx) == 1 and fx[0]["status"] == "done" and fx[0]["module"] == "*"
    assert fx[0]["role"] == "integrator-engineer" and fx[0]["tier"] == "deep"
    assert "1. Claim: criterion 1" in fx[0]["prompt"] and "mock correction" in fx[0]["prompt"]
    assert events(db, rid, "review_fix")[0]["wrong"] == 1
    redeploy = events(db, rid, "review_redeploy")
    assert len(redeploy) == 1 and redeploy[0]["round"] == 1
    assert [e["verification"] for e in events(db, rid, "review_round")] == ["failed", "passed"]
    oks = events(db, rid, "deploy_ok")
    names = [e["event"] for e in db.get_log("run:" + rid)
             if e["event"] in ("deploy_ok", "undeployed", "review_redeploy")]
    assert names == ["undeployed", "review_redeploy", "deploy_ok"] or names[-4:] == [
        "deploy_ok", "undeployed", "review_redeploy", "deploy_ok"], names
    assert len(oks) == 2
    assert len(events(db, rid, "undeployed")) == 1
    assert db.get_deployment(rid)["status"] == "ready"
    await engine.undeploy(rid)


async def test_fix_rounds_exhausted_stays_failed(dep, monkeypatch):
    db, engine, cfg = dep
    monkeypatch.setattr(config, "VERIFY_ROUNDS", 1)
    wrong_plan(engine)
    rid, run = await run_plan(db, engine)
    assert run["status"] == "done", run["error"]
    assert db.get_run(rid)["verification"] == "failed"
    assert [v["round"] for v in db.get_verifications(rid)] == [1, 2]
    assert len(fixers(db, rid)) == 1
    assert db.get_sandbox(rid)["status"] == "kept"
    assert db.get_deployment(rid)["status"] == "ready"
    await engine.undeploy(rid)


async def test_fixer_failure_keeps_failed(dep, monkeypatch):
    db, engine, cfg = dep
    monkeypatch.setattr(config, "VERIFY_ROUNDS", 1)
    wrong_plan(engine)
    monkeypatch.setenv("PIPEZ_MOCK_RUNNER_FAIL", "fix4")      # fixer plan id = fix<seq>
    rid, run = await run_plan(db, engine)
    assert run["status"] == "done", run["error"]
    assert db.get_run(rid)["verification"] == "failed"
    fx = fixers(db, rid)
    assert len(fx) == 1 and fx[0]["status"] == "failed"
    assert len(db.get_verifications(rid)) == 1
    assert events(db, rid, "review_fix_stopped")
    assert db.get_sandbox(rid)["status"] == "kept"
    await engine.undeploy(rid)


async def test_verify_rounds_zero_no_fixer(dep, monkeypatch):
    db, engine, cfg = dep
    monkeypatch.setattr(config, "VERIFY_ROUNDS", 0)
    wrong_plan(engine)
    rid, run = await run_plan(db, engine)
    assert run["status"] == "done", run["error"]
    assert db.get_run(rid)["verification"] == "failed"
    assert fixers(db, rid) == [] and len(db.get_verifications(rid)) == 1
    assert not events(db, rid, "review_fix")
    await engine.undeploy(rid)
