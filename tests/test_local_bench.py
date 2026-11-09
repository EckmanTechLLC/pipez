import json
import os

import pytest

from pipez import config, seed_prompts
from pipez.db import Database
from pipez.engine import Engine
from pipez.events import EventBus
from pipez.llm import LLM, plan_schema, decision_schema, MockLLM

from scripts import bench_local


def _with_local(monkeypatch, stages=()):
    tiers = dict(config.MODEL_TIERS)
    tiers["local"] = {"provider": "openai_compat", "model": "qwen",
                      "effort": "low", "base_url": "http://x"}
    monkeypatch.setattr(config, "MODEL_TIERS", tiers)
    monkeypatch.setattr(config, "LOCAL_BASE_URL", "")   # no probe
    monkeypatch.setattr(config, "LOCAL_ALLOWED_STAGES", list(stages))


def _engine(tmp_path):
    db = Database(str(tmp_path / "t.sqlite3"))
    seed_prompts.seed(db)
    return db, Engine(db, EventBus(), MockLLM())


def test_code_task_local_coerced_to_light(monkeypatch, tmp_path):
    _with_local(monkeypatch)
    db, engine = _engine(tmp_path)
    run_id = db.create_run("req")
    db.set_plan(run_id, {"architecture": "a", "repo_layout": "l", "modules": [],
                         "tasks": [{"id": "t1", "title": "T", "module": "m",
                                    "prompt": "p", "tier": "local",
                                    "acceptance": []}]}, {})
    db.set_brief(run_id, "brief", {})
    root = engine._materialise_plan(run_id)
    kid = db.get_children(root["id"])[0]
    assert kid["tier"] == "light"
    ev = [r for r in db.get_log("run:%s" % run_id) if r["event"] == "tier_coerced"]
    assert len(ev) == 1
    assert engine._leaf_model({"tier": "local"}) == config.MODEL_TIERS["light"]["model"]
    assert config.VERIFY_TIER != "local"


def test_planner_tiers_gated_by_stage(monkeypatch):
    _with_local(monkeypatch)
    enum = plan_schema()["properties"]["tasks"]["items"]["properties"]["tier"]["enum"]
    assert "local" not in enum
    denum = decision_schema()["properties"]["subtasks"]["items"]["properties"]["tier"]["enum"]
    assert "local" not in denum
    monkeypatch.setattr(config, "LOCAL_ALLOWED_STAGES", ["plan"])
    enum = plan_schema()["properties"]["tasks"]["items"]["properties"]["tier"]["enum"]
    assert "local" in enum
    assert "local" in config.offered_tiers("plan")
    assert "local" not in config.offered_tiers("decide")


async def test_plan_prompt_lists_allowed_tiers(monkeypatch, tmp_path):
    seen = []

    class Spy(MockLLM):
        async def plan(self, system, prompt, tier=None):
            seen.append(system)
            return await super().plan(system, prompt, tier)

    for stages, want in (([], False), (["plan"], True)):
        _with_local(monkeypatch, stages)
        db = Database(str(tmp_path / ("%d.sqlite3" % len(stages))))
        seed_prompts.seed(db)
        engine = Engine(db, EventBus(), Spy())
        run_id = db.create_run("req")
        db.set_brief(run_id, "brief", {})
        await engine._plan(run_id)
        allowed = seen[-1].rsplit("Allowed tiers:", 1)[1]
        assert ("local" in allowed) is want


def _stored_run(db, **kw):
    run_id = db.create_run("Build a thing", context="ctx")
    decisions = [{"key": "lang", "question": "Which?", "why_it_matters": "w",
                  "options": [{"value": "py", "consequences": "c"},
                              {"value": "go", "consequences": "d"}],
                  "proposed_default": "py", "required": True, "free_text": False,
                  "locked": False,
                  "prefill": {"value": "py", "source": "model", "evidence": {}}}]
    db.set_intake(run_id, decisions, {})
    db.set_answers(run_id, {"lang": "go"})
    return run_id


def test_prompt_builders_match_old_inline(tmp_path):
    db, engine = _engine(tmp_path)
    run_id = _stored_run(db)
    run = db.get_run(run_id)
    system, prompt = engine._intake_prompt(run)
    assert system == db.get_prompt("intake_discovery")
    assert prompt == "\n\n".join([
        "# Request\nBuild a thing", "# Project context\nctx",
        engine._known_keys_section(run["project_id"])])
    system, prompt = engine._brief_prompt(run, {"lang": "intake"})
    table = engine._decision_table(run["decisions"], run["answers"], {"lang": "intake"})
    assert system == db.get_prompt("intake_brief")
    assert prompt == "\n\n".join(["# Original request\nBuild a thing",
                                  "# Project context\nctx",
                                  "# Answered decisions\n%s" % table])


def test_bench_dry_run(monkeypatch, tmp_path, capsys):
    _with_local(monkeypatch)
    monkeypatch.setattr(config, "MOCK", False)
    db, _ = _engine(tmp_path)
    _stored_run(db)
    _stored_run(db)
    db._conn.commit()
    rc = bench_local.main(["--db", db.path, "--dry-run", "--out", str(tmp_path / "o")])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.count("plan: intake") == 2 and out.count("plan: brief") == 2
    assert "estimated spend" in out
    assert not (tmp_path / "o").exists()


def test_intake_tier_default_and_validation(monkeypatch):
    assert config.INTAKE_TIER == config.valid_tier(os.environ.get("PIPEZ_INTAKE_TIER", "deep"))
    assert config.valid_tier("standard") == "standard"
    assert config.valid_tier("nonsense") == "deep"


async def test_intake_uses_config_tier(monkeypatch, tmp_path):
    seen = []

    class Spy(MockLLM):
        async def intake(self, system, prompt, tier=None):
            seen.append(tier)
            return await super().intake(system, prompt, tier)

    db = Database(str(tmp_path / "i.sqlite3"))
    seed_prompts.seed(db)
    engine = Engine(db, EventBus(), Spy())
    monkeypatch.setattr(config, "INTAKE_TIER", "standard")
    run_id = db.create_run("req")
    await engine._intake(run_id)
    assert seen and seen[0] == config.MODEL_TIERS["standard"]


def test_bench_request_files_dry_run(monkeypatch, tmp_path, capsys):
    _with_local(monkeypatch)
    monkeypatch.setattr(config, "MOCK", False)
    db, _ = _engine(tmp_path)
    _stored_run(db)
    db._conn.commit()
    reqs = tmp_path / "reqs"
    reqs.mkdir()
    (reqs / "a.md").write_text("Build A\n")
    (reqs / "b.md").write_text("Build B\n")
    (reqs / "ignore.txt").write_text("no")
    rc = bench_local.main(["--db", db.path, "--dry-run", "--stages", "intake,brief",
                           "--tiers", "standard", "--reference-tier", "deep",
                           "--request-files", str(reqs), "--max-per-stage", "0"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.count("plan: intake run req:") == 2
    assert "req:a" in out and "req:b" in out
    assert "plan: brief" not in out
    assert "tiers deep,standard" in out


def test_agreement_intake_against_reference():
    def dec(key, default, **kw):
        return dict({"key": key, "proposed_default": default}, **kw)

    ref = {"decisions": [dec("lang", "py"), dec("db", "sqlite"), dec("ui", "cli")],
           "request_values": {"lang": "py", "db": "sqlite"}}
    out = {"decisions": [dec("lang", "py"), dec("db", "pg"), dec("auth", "none")],
           "request_values": {"lang": "py"}}
    a = bench_local.agreement_intake(out, {}, ref)
    assert a["key_jaccard"] == pytest.approx(2 / 4)
    assert a["default_agree"] == pytest.approx(1 / 2)
    assert a["locked_agree"] == pytest.approx(1 / 2)
    # stored run: locked decisions are the reference set
    run = {"decisions": [dec("lang", "py", locked=True), dec("db", "pg", locked=False)]}
    b = bench_local.agreement_intake(out, run)
    assert b["locked_agree"] == 1.0
    assert b["key_jaccard"] == pytest.approx(2 / 3)
    assert bench_local.agreement_intake({}, {}, {})["locked_agree"] == 1.0


# -- task 24: plan prompt builder, revision tier, plan bench -------------------

def _old_inline_plan_prompt(db, run):
    revisions = (run.get("plan_meta") or {}).get("revisions") or []
    system = (db.get_prompt("plan_architect") or "") + (
        "\n\nHARD LIMITS: at most %d tasks and at most %d modules. "
        "Merge small tasks; a task is a unit one engineer completes "
        "in one sitting, not a function."
        % (config.PLAN_MAX_TASKS, config.PLAN_MAX_MODULES))
    parts = ["# Original request\n%s" % run["request"]]
    if run.get("context"):
        parts.append("# Project context\n%s" % run["context"])
    parts.append("# Build brief (binding)\n%s" % run.get("brief"))
    system += "\n\nAllowed tiers: %s." % ", ".join(config.offered_tiers("plan"))
    if run.get("plan") and revisions:
        parts.append("# Previous plan\n%s" % json.dumps(run["plan"], indent=2))
        parts.append("REVISION NOTE (from the human reviewer; revise "
                     "the previous plan accordingly):\n%s" % revisions[-1]["note"])
    return system, "\n\n".join(parts)


def _plan_run(db, revisions=None, parent=None):
    run_id = db.create_run("Build a thing", context="ctx", parent_run_id=parent)
    db.set_brief(run_id, "the brief", {})
    plan = {"architecture": "a", "repo_layout": "l", "modules": [{"name": "m"}],
            "scaffold_commands": ["mkdir x"], "test_command": "pytest",
            "deploy": {"target": "vm"},
            "tasks": [{"id": "t1", "title": "T", "module": "m", "prompt": "p",
                       "tier": "light", "acceptance": []}]}
    db.set_plan(run_id, plan, {"revisions": revisions or []})
    return run_id


def test_plan_prompt_matches_old_inline(tmp_path):
    db, engine = _engine(tmp_path)
    first = db.get_run(_plan_run(db))
    assert not engine._is_revision(first)
    assert engine._plan_prompt(first) == _old_inline_plan_prompt(db, first)
    rev = db.get_run(_plan_run(db, [{"note": "use go"}]))
    assert engine._is_revision(rev)
    system, prompt = engine._plan_prompt(rev)
    assert (system, prompt) == _old_inline_plan_prompt(db, rev)
    assert "# Previous plan" in prompt and "REVISION NOTE" in prompt and "use go" in prompt
    assert "HARD LIMITS" in system and "Allowed tiers" in system


async def test_plan_revision_uses_revision_tier_and_effort(monkeypatch, tmp_path):
    seen = []

    class Spy(MockLLM):
        async def plan(self, system, prompt, tier=None):
            seen.append(tier)
            return await super().plan(system, prompt, tier)

    db = Database(str(tmp_path / "r.sqlite3"))
    seed_prompts.seed(db)
    engine = Engine(db, EventBus(), Spy())
    monkeypatch.setattr(config, "PLAN_REVISION_TIER", "standard")
    monkeypatch.setattr(config, "PLAN_REVISION_EFFORT", "low")
    monkeypatch.setattr(config, "PLAN_EFFORT", "high")
    first = db.create_run("req")
    db.set_brief(first, "brief", {})
    await engine._plan(first)
    assert seen[-1]["model"] == config.MODEL_TIERS["deep"]["model"]
    assert seen[-1]["effort"] == "high"
    rev = _plan_run(db, [{"note": "change"}])
    await engine._plan(rev)
    assert seen[-1]["model"] == config.MODEL_TIERS["standard"]["model"]
    assert seen[-1]["effort"] == "low"
    ev = [r for r in db.get_log("run:%s" % rev) if r["event"] == "plan_call"]
    assert ev and json.loads(ev[-1]["detail"]) == {
        "revision": True, "tier": "standard", "effort": "low"}


def test_plan_revision_config_defaults():
    assert config.PLAN_REVISION_TIER == config.valid_tier(
        os.environ.get("PIPEZ_PLAN_REVISION_TIER", "deep"))
    assert config.PLAN_REVISION_TIER in config.MODEL_TIERS
    assert config.PLAN_REVISION_EFFORT == (
        os.environ.get("PIPEZ_PLAN_REVISION_EFFORT") or config.PLAN_EFFORT)
    assert isinstance(config.PLAN_REVISION_EFFORT, str) and config.PLAN_REVISION_EFFORT


def test_bench_plan_dry_run(monkeypatch, tmp_path, capsys):
    _with_local(monkeypatch)
    monkeypatch.setattr(config, "MOCK", False)
    db, _ = _engine(tmp_path)
    _plan_run(db)
    _plan_run(db, [{"note": "n"}])
    db._conn.commit()
    rc = bench_local.main(["--db", db.path, "--dry-run", "--stages", "plan,plan_revision",
                           "--tiers", "deep@medium,standard", "--out", str(tmp_path / "o")])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.count("plan: plan ") == 2 and out.count("plan: plan_revision") == 1
    assert "deep@medium,standard" in out
    assert "calls: 6" in out


def test_agreement_plan():
    task = lambda i: {"id": "t%d" % i}
    stored = {"tasks": [task(1), task(2), task(3)], "deploy": {"target": "vm"}}
    out = {"tasks": [task(1), task(2)], "modules": [{"name": "a"}],
           "deploy": {"target": "none"}, "test_command": "pytest",
           "scaffold_commands": ["npm ci", "git init"]}
    a = bench_local.agreement_plan(out, {"plan": stored, "parent_run_id": "p"})
    assert a["tasks"] == 2 and a["modules"] == 1
    assert a["deploy_target"] == "none" and a["deploy_target_match"] is False
    assert a["task_count_delta"] == -1
    assert a["scaffold_forbidden"] is True and a["has_test_command"] is True
    assert a["plan_chars"] > 0
    b = bench_local.agreement_plan(out, {"plan": stored})
    assert b["scaffold_forbidden"] is False
    c = bench_local.agreement_plan(out, {"plan": stored}, {"tasks": [task(1)] * 2,
                                                          "deploy": {"target": "none"}})
    assert c["task_count_delta"] == 0 and c["deploy_target_match"] is True


# ---- plan-gate metrics and gate rounds (stub planner, no network) ----------
def _gate_plan(bad=True):
    """Two modules; with bad=True the consumer's task has no dependency on
    the provider's (a dep_coverage error)."""
    return {"architecture": "a", "stack": [], "repo_layout": "",
            "scaffold_commands": [], "build_command": "", "test_command": "t",
            "deploy": {"target": "none"},
            "modules": [
                {"name": "core", "depends_on": [], "files": ["core.py"],
                 "contract": "Provides `make_thing(size)` returning a Thing."},
                {"name": "app", "depends_on": ["core"], "files": ["app.py"],
                 "contract": "Provides `run_app()`; calls `make_thing(size)`."}],
            "tasks": [
                {"id": "T1", "title": "Core", "module": "core", "depends_on": [],
                 "tier": "light", "prompt": "Implement make_thing(size) in core.py. " * 20,
                 "acceptance": "pytest passes"},
                {"id": "T2", "title": "App", "module": "app",
                 "depends_on": [] if bad else ["T1"], "tier": "light",
                 "prompt": "Implement run_app in app.py using the core module. " * 20,
                 "acceptance": "pytest passes"}]}


class _StubLLM:
    def __init__(self, plans):
        self.plans, self.prompts = list(plans), []
        self.intake = self.brief = None     # one_call looks every stage up

    async def plan(self, system, prompt, tier):
        self.prompts.append(prompt)
        plan = self.plans.pop(0) if len(self.plans) > 1 else self.plans[0]
        if isinstance(plan, Exception):
            raise plan
        return plan, {"input_tokens": 10, "output_tokens": 5}


async def _gate_call(tmp_path, plans, rounds):
    db, engine = _engine(tmp_path)
    run = {"id": "r1", "request": "req", "context": "", "brief": "brief",
           "plan": _gate_plan(bad=False), "plan_meta": {}}
    llm = _StubLLM(plans)
    rec = await bench_local.one_call(
        llm, "plan", "light", "sys", "prompt", run, engine=engine,
        gate_rounds=rounds, save_output=True)
    return rec, llm


async def test_bench_gate_metrics(tmp_path):
    rec, llm = await _gate_call(tmp_path, [_gate_plan(bad=True)], 0)
    assert len(llm.prompts) == 1
    assert rec["gate_errors"] == 1 and rec["gate_codes"] == ["dep_coverage"]
    assert rec["ref_gate_errors"] == 0
    assert rec["gate_violations"] and "gate_rounds_used" not in rec


async def test_bench_gate_round_fixes(tmp_path):
    rec, llm = await _gate_call(
        tmp_path, [_gate_plan(bad=True), _gate_plan(bad=False)], 2)
    assert len(llm.prompts) == 2
    assert "AUTOMATED PLAN GATE" in llm.prompts[1]
    assert rec["gate_rounds_used"] == 1 and rec["gate_passed"] is True
    assert rec["gate_round_errors"] == [1, 0] and rec["gate_errors"] == 0
    assert rec["input_tokens"] == 20 and rec["output_tokens"] == 10
    assert rec["output"] == _gate_plan(bad=True)
    assert rec["final_output"] == _gate_plan(bad=False)
    assert len(rec["round_outputs"]) == 2


async def test_bench_gate_round_keeps_failing(tmp_path):
    rec, llm = await _gate_call(tmp_path, [_gate_plan(bad=True)], 2)
    assert len(llm.prompts) == 3
    assert rec["gate_rounds_used"] == 2 and rec["gate_passed"] is False
    assert rec["gate_round_errors"] == [1, 1, 1]


async def test_bench_gate_round_error(tmp_path):
    rec, llm = await _gate_call(
        tmp_path, [_gate_plan(bad=True), RuntimeError("boom")], 2)
    assert rec["gate_passed"] is False and "boom" in rec["gate_round_error"]
    assert rec["gate_rounds_used"] == 0


def test_bench_plan_table_gate_columns():
    r = {"stage": "plan", "tier": "t", "ok": True, "seconds": 1.0, "cost": 0.1,
         "output_tokens": 5, "tasks": 2, "task_count_delta": 0,
         "deploy_target_match": True, "scaffold_forbidden": False,
         "gate_errors": 1, "ref_gate_errors": 0, "gate_rounds_used": 1,
         "gate_passed": True}
    out = bench_local.plan_table([r])
    assert "gate-err" in out and "ref-err" in out and "pass%" in out and "rounds" in out
