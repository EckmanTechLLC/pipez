"""Integrator: build/test on `integration`, ff main, tag, export, fail paths."""
import json
import os
import subprocess
import tarfile

import pytest

from pipez import config
from pipez.engine import Engine

from tests.test_code_leaf import (  # noqa: F401  (env is a fixture)
    PlanLLM, RecSandbox, env, git, run_plan)


def events(db, rid, name):
    root = next(t for t in db.get_tasks(rid) if t["parent_id"] is None)
    return [json.loads(e["detail"]) for e in db.get_log(root["id"]) if e["event"] == name]


@pytest.fixture
def art(env, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_ARTIFACTS", True)
    monkeypatch.setattr(config, "ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    calls = []

    async def spy(self, *a, **k):
        calls.append(a)
    monkeypatch.setattr(Engine, "_render_artifacts", spy)
    return env, calls


async def test_happy_path(art):
    (db, engine, llm, _), calls = art
    rid, run = await run_plan(db, engine)
    assert run["status"] == "done", run["error"]
    assert len(events(db, rid, "integrate_ok")) == 1
    assert [e["cmd"] for e in events(db, rid, "integrate_build")] == ["true"]
    test = events(db, rid, "integrate_test")[0]
    assert test["rc"] == 0 and "passed" in test["tail"]
    assert calls == []                       # no LLM rendering for code runs
    names = sorted(a["filename"] for a in db.get_artifacts(rid))
    assert names == ["repo-%s.bundle" % rid, "repo-%s.tar.gz" % rid]
    for a in db.get_artifacts(rid):
        assert os.path.getsize(a["path"]) == a["bytes"] > 0
    with tarfile.open(os.path.join(config.ARTIFACTS_DIR, rid, "repo-%s.tar.gz" % rid)) as t:
        assert {"core/t1.py", "api/t3.py"} <= set(t.getnames())
    assert db.get_sandbox(rid)["status"] == "destroyed"
    # the output came from the root synthesis, which saw the Integration section
    assert json.loads(run["result"])["output"]


async def test_main_and_tag(art, monkeypatch):
    (db, engine, llm, _), _ = art
    monkeypatch.setattr(config, "SANDBOX_KEEP", True)
    rid, run = await run_plan(db, engine)
    wd = db.get_sandbox(rid)["name"]
    assert git(wd, "tag", "-l", "pipez/%s" % rid) == "pipez/%s" % rid
    assert git(wd, "rev-parse", "main") == git(wd, "rev-parse", "integration")
    assert git(wd, "rev-parse", "--abbrev-ref", "HEAD") == "integration"


async def test_synthesis_prompt_has_integration(art):
    (db, engine, llm, _), _ = art
    seen = []
    orig = llm.synthesize

    async def spy(system, prompt, tier=None):
        seen.append(prompt)
        return await orig(system, prompt, tier)
    llm.synthesize = spy
    rid, run = await run_plan(db, engine)
    assert run["status"] == "done", run["error"]
    root_prompt = seen[-1]
    assert "# Integration" in root_prompt and "pipez/%s" % rid in root_prompt
    assert "python3 -m pytest -q" in root_prompt


async def test_failing_test_command(art, monkeypatch):
    (db, engine, llm, _), calls = art
    orig = PlanLLM.plan

    async def plan(self, system, prompt, tier=None):
        out, usage = await orig(self, system, prompt, tier)
        out["test_command"] = "echo boom-output; false"
        return out, usage
    monkeypatch.setattr(PlanLLM, "plan", plan)
    rid, run = await run_plan(db, engine)
    assert run["status"] == "failed" and "boom-output" in run["error"]
    assert len(events(db, rid, "integrate_failed")) == 1
    assert events(db, rid, "integrate_test")[0]["rc"] == 1
    assert not events(db, rid, "integrate_ok")
    assert db.get_sandbox(rid)["status"] == "kept"
    wd = db.get_sandbox(rid)["name"]
    assert git(wd, "tag", "-l", "pipez/*") == ""
    assert git(wd, "rev-parse", "main", "integration") != ""   # main untouched
    assert db.get_artifacts(rid) == []
    assert calls == []
    assert not os.path.exists(os.path.join(config.ARTIFACTS_DIR, rid))


async def test_dirty_integration(art, monkeypatch):
    (db, engine, llm, _), _ = art
    orig = RecSandbox._exec
    state = {"done": False}

    async def exec_(self, line, stdin, timeout):
        if "status --porcelain" in line and not state["done"]:
            state["done"] = True
            repo = os.path.join(self.workdir, "repo")
            open(os.path.join(repo, "stray.txt"), "w").write("x")
        return await orig(self, line, stdin, timeout)
    monkeypatch.setattr(RecSandbox, "_exec", exec_)
    rid, run = await run_plan(db, engine)
    assert run["status"] == "failed" and "dirty working tree" in run["error"]
    assert "dirty working tree" in events(db, rid, "integrate_failed")[0]["error"]
    assert not events(db, rid, "integrate_build")
    assert db.get_sandbox(rid)["status"] == "kept"
    assert db.get_artifacts(rid) == []
