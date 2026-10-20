"""Child runs of a plan run are seeded from the parent's repo bundle."""
import json
import os

import pytest

from pipez import config
from pipez.engine import ScaffoldError

from tests.test_code_leaf import env, run_plan, git, RecSandbox, PlanLLM  # noqa: F401
from tests.test_intake import wait_status

CLONE = "git clone -q"


@pytest.fixture
def arts(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ARTIFACTS_DIR", str(tmp_path / "arts"))


def events(db, rid, name):
    return [json.loads(e["detail"]) for e in db.get_log("run:" + rid) if e["event"] == name]


async def parent_and_child(db, engine):
    parent, run = await run_plan(db, engine)
    assert run["status"] == "done", run["error"]
    child = db.create_run("add a thing", None, parent)
    return parent, child


def stage_plan(db, child, scaffold=None):
    plan = dict(db.get_run(child).get("plan") or {})
    plan.update(db.get_run(db.get_run(child)["parent_run_id"])["plan"])
    plan["scaffold_commands"] = scaffold if scaffold is not None else ["mkdir -p extra"]
    db.set_plan(child, plan, {})
    return plan


async def start_sandbox(engine, child):
    sb = RecSandbox(child)
    await sb.provision()
    engine._sandboxes[child] = sb
    return sb


async def test_child_seeded_from_bundle(env, arts):
    db, engine, llm, _ = env
    parent, child = await parent_and_child(db, engine)
    assert os.path.isfile(os.path.join(config.ARTIFACTS_DIR, parent, "repo-%s.bundle" % parent))
    stage_plan(db, child)
    sb = await start_sandbox(engine, child)
    RecSandbox.lines = []
    await engine._init_workspace(child)
    assert any(CLONE in l and "parent.bundle" in l for l in RecSandbox.lines)
    assert any("git checkout -q main" in l for l in RecSandbox.lines)
    assert not any("git init" in l for l in RecSandbox.lines)
    ev = events(db, child, "workspace_seeded")
    assert ev and ev[0]["parent"] == parent and len(ev[0]["head"]) == 40
    assert git(sb.workdir, "rev-parse", "--abbrev-ref", "HEAD") == "integration"
    assert git(sb.workdir, "rev-parse", "pipez/" + parent)
    tree = git(sb.workdir, "ls-tree", "-r", "--name-only", "main").splitlines()
    assert "core/t1.py" in tree
    assert "child of " + parent in git(sb.workdir, "log", "-1", "--format=%s")
    await sb.teardown(False)


async def test_missing_bundle_falls_back_to_init(env, arts):
    db, engine, llm, _ = env
    parent = db.create_run("p", None)
    child = db.create_run("c", None, parent)
    stage_plan_plan = {"architecture": "", "stack": [], "repo_layout": "", "modules": [],
                       "tasks": [], "scaffold_commands": ["mkdir -p extra"]}
    db.set_plan(child, stage_plan_plan, {})
    sb = await start_sandbox(engine, child)
    RecSandbox.lines = []
    await engine._init_workspace(child)
    assert events(db, child, "workspace_seed_missing")
    assert not events(db, child, "workspace_seeded")
    assert any("git init" in l for l in RecSandbox.lines)
    await sb.teardown(False)


async def test_child_plan_prompt_has_existing_repository(env, arts):
    db, engine, llm, _ = env
    parent, child = await parent_and_child(db, engine)
    section = engine._existing_repo_section(db.get_run(child))
    assert section.startswith("# Existing repository")
    assert "pipez/" + parent in section and "branch `main`" in section
    assert "app/" in section and "python3 -m pytest -q" in section
    assert "Do NOT re-scaffold" in section
    # no section for a run without a parent bundle
    assert engine._existing_repo_section(db.get_run(parent)) == ""
    seen = {}
    orig = llm.plan

    async def spy(system, prompt, tier=None):
        seen["prompt"] = prompt
        return await orig(system, prompt, tier)
    llm.plan = spy
    db.set_brief(child, "brief", {})
    await engine._plan(child)
    assert "# Existing repository" in seen["prompt"] and "pipez/" + parent in seen["prompt"]


async def test_checkout_scaffold_rejected_before_running(env, arts):
    db, engine, llm, _ = env
    parent, child = await parent_and_child(db, engine)
    stage_plan(db, child, ["mkdir -p extra", "git checkout -b x pipez/%s" % parent])
    sb = await start_sandbox(engine, child)
    RecSandbox.lines = []
    with pytest.raises(ScaffoldError, match="not allowed on a seeded workspace"):
        await engine._init_workspace(child)
    assert events(db, child, "scaffold_filtered")
    assert not any("mkdir -p extra" in l or CLONE in l for l in RecSandbox.lines)
    assert not events(db, child, "scaffold")
    await sb.teardown(False)
