import json

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
    assert config.INTAKE_TIER == "deep"
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
