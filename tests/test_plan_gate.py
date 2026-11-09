import importlib
import json
import os

import pytest

from pipez import config, plangate, seed_prompts
from pipez.db import Database
from pipez.engine import Engine
from pipez.events import EventBus

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "plans")


def load(name):
    with open(os.path.join(FIX, name)) as f:
        return json.load(f)


def errors(v):
    return [(x["code"], x["subject"]) for x in v if x["severity"] == "error"]


def test_opus_ttt_only_dep_coverage_t7():
    v = plangate.check_plan(load("58b9853f2c47-opus.json"))
    assert errors(v) == [("dep_coverage", "T7")]
    assert "T9" not in v[0]["message"] or "server" in v[0]["message"]
    assert all(x["hint"] and x["message"] for x in v)


def test_opus_shortener_no_errors():
    assert errors(plangate.check_plan(load("c7213225e46a-opus.json"))) == []


def test_qwen_ttt_defects():
    e = errors(plangate.check_plan(load("58b9853f2c47-qwen.json")))
    # module `client` has depends_on [] in this plan, so the blind-build gap
    # shows on the server tasks (T1 has no deps at all).
    assert ("dep_coverage", "T1") in e
    assert ("contract_interface", "client") in e
    assert ("contract_interface", "server") in e
    assert any(c == "task_prompt_thin" for c, _ in e)
    assert len(e) >= 4


def test_gptoss_module_none():
    plan = load("c7213225e46a-gptoss.json")
    e = errors(plangate.check_plan(plan))
    none = [t["id"] for t in plan["tasks"] if t["module"] == "none"]
    assert none
    for tid in none:
        assert ("task_module_unknown", tid) in e


def test_module_dep_unknown():
    plan = _good_plan()
    plan["modules"][1]["depends_on"] = ["core", "ghost"]
    assert ("module_dep_unknown", "api") in errors(plangate.check_plan(plan))


def _good_plan(chain=False):
    core_prompt = ("Implement `src/core.py` exporting `add(a, b)` and `mul(a, b)` exactly as "
                   "the core contract states. add returns the integer sum, mul the product. "
                   "Reject non-integer input with TypeError. Write tests in tests/test_core.py "
                   "covering zero, negative and large values, and run them with pytest. "
                   "Keep the module free of I/O and of third-party dependencies. " * 2)
    api_prompt = ("Implement `src/api.py` exporting `handle(request)` which calls `add(a, b)` "
                  "from the core module and returns `{\"sum\": n}` for `POST /sum` with a JSON "
                  "body. Return status 400 for a malformed body. Write tests in tests/test_api.py "
                  "and run them with pytest. Do not reimplement arithmetic. " * 2)
    plan = {
        "modules": [
            {"name": "core", "path": "src", "purpose": "math", "depends_on": [],
             "contract": "Files owned: src/core.py, tests/test_core.py. Exports `add(a, b)` and "
                         "`mul(a, b)`. Tests: pytest tests/test_core.py."},
            {"name": "api", "path": "src", "purpose": "http", "depends_on": ["core"],
             "contract": "Files owned: src/api.py, tests/test_api.py. Uses core `add(a, b)`. "
                         "POST /sum takes {\"a\": int, \"b\": int} and returns {\"sum\": int}. "
                         "Tests: pytest tests/test_api.py."},
        ],
        "tasks": [
            {"id": "T1", "title": "Core", "module": "core", "prompt": core_prompt,
             "depends_on": [], "acceptance": ["`pytest tests/test_core.py` passes"]},
            {"id": "T2", "title": "Api", "module": "api", "prompt": api_prompt,
             "depends_on": ["T1"], "acceptance": ["`pytest tests/test_api.py` passes",
                                                   "POST /sum returns 200"]},
        ],
    }
    if chain:
        plan["modules"].append(
            {"name": "app", "path": "src", "purpose": "entry", "depends_on": ["core"],
             "contract": "Files owned: src/app.py. Exports `main()`. Calls core `add(a, b)`. "
                         "Tests: pytest tests/test_app.py."})
        plan["tasks"].append(
            {"id": "T3", "title": "App", "module": "app",
             "prompt": ("Implement `src/app.py` with `main()` wiring the core module "
                        "`add(a, b)` into a command line entry point; print the sum. "
                        "Add tests/test_app.py and run pytest. " * 4),
             "depends_on": ["T2"], "acceptance": ["`pytest tests/test_app.py` passes"]})
    return plan


def test_minimal_plan_passes():
    assert plangate.check_plan(_good_plan()) == []


def test_transitive_dependency_satisfies_coverage():
    plan = _good_plan(chain=True)
    # T3's module depends on core; T3 -> T2 -> T1 is only transitive.
    plan["tasks"][2]["prompt"] = plan["tasks"][2]["prompt"].replace("add(a, b)", "the sum")
    assert plan["tasks"][2]["depends_on"] == ["T2"]
    assert not [x for x in plangate.check_plan(plan) if x["code"] == "dep_coverage"]
    # and cutting the chain makes the same plan fail
    plan["tasks"][2]["depends_on"] = []
    plan["tasks"][2]["prompt"] += " Wire it up with the core module helpers."
    assert ("dep_coverage", "T3") in errors(plangate.check_plan(plan))


def test_format_note_one_line_per_violation():
    v = plangate.check_plan(load("58b9853f2c47-qwen.json"))
    note = plangate.format_note(v)
    lines = note.split("\n")
    assert lines[0].startswith("AUTOMATED PLAN GATE")
    assert len(lines) == len(v) + 1
    assert any(l.startswith("- [ERROR] dep_coverage (T1):") for l in lines)
    assert any(l.startswith("- [WARN] ") for l in lines)


def test_heuristics_folded_in():
    plan = {"modules": [], "tasks": [
        {"id": "t1", "title": "Live deploy to the real Ubuntu VM", "prompt": "x",
         "acceptance": ["The report gives the npm ci rc"]}]}
    codes = {x["code"] for x in plangate.check_plan(plan)}
    assert {"task_touches_deployment", "process_acceptance_criteria"} <= codes


# ---- engine ----------------------------------------------------------------

class PlanLLM:
    def __init__(self, plans):
        self.plans, self.calls = list(plans), []

    async def plan(self, system, prompt, tier=None):
        self.calls.append({"system": system, "prompt": prompt, "tier": tier})
        i = min(len(self.calls) - 1, len(self.plans) - 1)
        return (json.loads(json.dumps(self.plans[i])),
                {"input_tokens": 1, "output_tokens": 1, "model": "stub"})


@pytest.fixture
def gate_cfg(monkeypatch):
    """Pin the gate settings regardless of the environment/.env."""
    def set_(mode, rounds=2):
        monkeypatch.setattr(config, "PLAN_GATE_MODE", mode)
        monkeypatch.setattr(config, "PLAN_GATE_MAX_ROUNDS", rounds)
        monkeypatch.setattr(config, "PLAN_GATE_MIN_PROMPT_CHARS", 400)
    monkeypatch.setattr(config, "ENABLE_ARTIFACTS", False)
    return set_


def _engine(tmp_path, plans):
    db = Database(str(tmp_path / "g.sqlite3"))
    seed_prompts.seed(db)
    llm = PlanLLM(plans)
    engine = Engine(db, EventBus(), llm)
    rid = db.create_run("build a thing")
    return db, engine, llm, rid


def _bad_plan():
    p = _good_plan()
    p["tasks"][1]["depends_on"] = []
    p["tasks"][1]["prompt"] = "Do the api."
    return p


async def test_revise_converges(tmp_path, gate_cfg):
    gate_cfg("revise")
    db, engine, llm, rid = _engine(tmp_path, [_bad_plan(), _good_plan()])
    await engine._plan(rid)
    run = db.get_run(rid)
    assert run["status"] == "awaiting_plan"
    gate = run["plan_meta"]["gate"]
    assert gate["passed"] is True and gate["mode"] == "revise"
    assert len(gate["rounds"]) == 2
    assert gate["rounds"][0]["errors"] > 0 and gate["rounds"][1]["errors"] == 0
    rows = [r for r in db.get_log("run:%s" % rid) if r["event"] == "plan_gate_revise"]
    assert len(rows) == 1
    assert len(llm.calls) == 2
    second = llm.calls[1]["prompt"]
    assert "AUTOMATED PLAN GATE" in second and "# Previous plan" in second
    assert "Do the api." in second
    assert llm.calls[1]["system"] == llm.calls[0]["system"]
    assert run["plan_meta"]["warnings"] == []


async def test_revise_bounded(tmp_path, gate_cfg):
    gate_cfg("revise", rounds=2)
    db, engine, llm, rid = _engine(tmp_path, [_bad_plan()])
    await engine._plan(rid)
    run = db.get_run(rid)
    assert run["status"] == "awaiting_plan"
    gate = run["plan_meta"]["gate"]
    assert gate["passed"] is False and len(gate["rounds"]) == 3
    assert len(llm.calls) == 3
    w = run["plan_meta"]["warnings"]
    assert w and w[0].startswith("[") and "task_prompt_thin" in " ".join(w)


async def test_warn_mode_single_call(tmp_path, gate_cfg):
    gate_cfg("warn")
    db, engine, llm, rid = _engine(tmp_path, [_bad_plan(), _good_plan()])
    await engine._plan(rid)
    run = db.get_run(rid)
    assert len(llm.calls) == 1
    assert run["status"] == "awaiting_plan"
    assert any("[task_prompt_thin] T2" in w for w in run["plan_meta"]["warnings"])
    assert run["plan_meta"]["gate"]["passed"] is False
    assert len(run["plan_meta"]["gate"]["rounds"]) == 1


async def test_off_mode_no_gate(tmp_path, gate_cfg):
    gate_cfg("off")
    db, engine, llm, rid = _engine(tmp_path, [_bad_plan()])
    await engine._plan(rid)
    meta = db.get_run(rid)["plan_meta"]
    assert "gate" not in meta and len(llm.calls) == 1


def test_gate_config_defaults(monkeypatch):
    for k in ("PIPEZ_PLAN_GATE", "PIPEZ_PLAN_GATE_ROUNDS", "PIPEZ_PLAN_GATE_MIN_PROMPT_CHARS"):
        monkeypatch.setenv(k, "")
    try:
        monkeypatch.setenv("PIPEZ_PLAN_GATE", "bogus")
        monkeypatch.setenv("PIPEZ_PLAN_GATE_ROUNDS", "2")
        monkeypatch.setenv("PIPEZ_PLAN_GATE_MIN_PROMPT_CHARS", "400")
        c = importlib.reload(config)
        assert (c.PLAN_GATE_MODE, c.PLAN_GATE_MAX_ROUNDS, c.PLAN_GATE_MIN_PROMPT_CHARS) == ("warn", 2, 400)
        monkeypatch.setenv("PIPEZ_PLAN_GATE", "Revise")
        assert importlib.reload(config).PLAN_GATE_MODE == "revise"
    finally:
        monkeypatch.undo()
        importlib.reload(config)


# ---- task 34: module kinds, thinness severity, round budget -----------------

def _mod(name, path, contract, deps=()):
    return {"name": name, "path": path, "purpose": "x", "depends_on": list(deps),
            "contract": contract}


@pytest.mark.parametrize("module,kind", [
    (_mod("core", "src", "Files owned: src/core.py. Exports `add(a, b)`."), "code"),
    (_mod("web", "src", "Files owned: src/web.js, test/web.test.js."), "code"),
    (_mod("apiTests", "test", "Files owned: test/api.test.js."), "tests"),
    (_mod("suite", "tests/", "Owned files: tests/test_a.py, tests/test_b.py."), "tests"),
    (_mod("tests", "", "Covers the whole service."), "tests"),
    (_mod("docs", "README.md", "Files owned: README.md, LICENSE."), "docs"),
    (_mod("docs", "", "Files owned: README.md, LICENSE, docs/usage.md."), "docs"),
    (_mod("infra", "Dockerfile", "CMD [\"node\",\"src/server.js\"]; owns .dockerignore."), "infra"),
    (_mod("ops", "", "Files owned: docker-compose.yml, deploy/run.sh, Makefile."), "infra"),
    (_mod("misc", "", "Does things."), "unknown"),
])
def test_module_kind(module, kind):
    assert plangate.module_kind(module) == kind


def test_noncode_modules_skip_contract_interface():
    plan = _good_plan()
    plan["modules"] += [
        _mod("tests", "tests/", "Files owned: tests/test_core.py. Runs pytest.", ["core"]),
        _mod("infra", "Dockerfile", "Dockerfile based on python:3.12.", []),
        _mod("docs", "README.md", "Files owned: README.md, LICENSE.", ["core"])]
    c = [x for x in plangate.check_plan(plan) if x["code"] == "contract_interface"]
    assert c == []


def test_dependency_into_noncode_and_task_on_docs_exempt():
    plan = _good_plan()
    plan["modules"].append(_mod("docs", "README.md", "Files owned: README.md.", ["core"]))
    plan["modules"][1]["depends_on"] = ["core", "docs"]
    plan["tasks"].append({"id": "T3", "title": "Readme", "module": "docs", "prompt": "Write it.",
                          "depends_on": [], "acceptance": ["README.md exists"]})
    got = [x for x in plangate.check_plan(plan) if x["severity"] == "error"]
    assert not [x for x in got if x["code"] in ("dep_coverage", "task_prompt_thin")]


def test_tests_module_consumer_named_by_test_path():
    plan = _good_plan()
    plan["modules"].append(_mod(
        "tests", "tests/", "Files owned: tests/test_all.py. Runs with pytest.", ["core"]))
    plan["tasks"].append({
        "id": "T3", "title": "Tests", "module": "tests", "depends_on": ["T1"],
        "prompt": "Write tests/test_all.py covering `src/core.py` add and mul.",
        "acceptance": ["`pytest tests/test_all.py` passes"]})
    got = [x for x in plangate.check_plan(plan) if x["severity"] == "error"]
    assert got == []
    # a short tests prompt naming nothing from the module or its deps is an error
    plan["tasks"][2]["prompt"] = "Write the tests."
    assert ("task_prompt_thin", "T3") in errors(plangate.check_plan(plan))


def _thin(prompt):
    plan = _good_plan()
    plan["tasks"][0]["prompt"] = prompt
    return [x for x in plangate.check_plan(plan)
            if x["code"] == "task_prompt_thin" and x["subject"] == "T1"]


def test_thin_severity():
    named = "Implement `src/core.py` " + "x " * 140
    assert 250 < len(named) < config.PLAN_GATE_MIN_PROMPT_CHARS
    got = _thin(named)
    assert [x["severity"] for x in got] == ["warn"]
    assert "characters" in got[0]["message"] and "names no" not in got[0]["message"]
    nameless = "Write the thing " + "x " * 145
    assert 250 < len(nameless) < config.PLAN_GATE_MIN_PROMPT_CHARS
    got = _thin(nameless)
    assert [x["severity"] for x in got] == ["error"]
    assert "characters" in got[0]["message"] and "names no symbol" in got[0]["message"]
    long_nameless = "Write the thing " + "x " * 300
    assert len(long_nameless) > config.PLAN_GATE_MIN_PROMPT_CHARS
    got = _thin(long_nameless)
    assert [x["severity"] for x in got] == ["warn"]
    assert "names no symbol" in got[0]["message"]


def test_gate_rounds_default_is_three(monkeypatch):
    monkeypatch.delenv("PIPEZ_PLAN_GATE_ROUNDS", raising=False)
    try:
        importlib.reload(config)
        assert config.PLAN_GATE_MAX_ROUNDS == 3
    finally:
        importlib.reload(config)


# Saved first-round gpt-oss plans of the convergence bench (the bench stores the
# first plan's `output`, not the post-revision plan, so these still carry the
# defects the revision rounds fixed: unknown task modules, missing wire shapes).
def test_gptoss_replay_noncode_false_positives_gone():
    for name in ("c7213225e46a", "60cd5166f16a", "c6973d74ac0e"):
        plan = load("%s-gptoss-r2.json" % name)
        kinds = {m["name"]: plangate.module_kind(m) for m in plan["modules"]}
        bad = [x for x in plangate.check_plan(plan)
               if x["code"] == "contract_interface"
               and kinds.get(x["subject"]) in ("tests", "infra", "docs")]
        assert bad == [], name
    plan = load("c7213225e46a-gptoss-r2.json")
    assert plangate.module_kind(
        [m for m in plan["modules"] if m["name"] == "infra"][0]) == "infra"
    plan = load("c6973d74ac0e-gptoss-r2.json")
    e = errors(plangate.check_plan(plan))
    assert ("contract_interface", "tests") not in e
    assert ("dep_coverage", "T7") in e  # tests->money edge into code stays checked
