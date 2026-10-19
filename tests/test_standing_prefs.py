import asyncio

import pytest

from pipez import config, seed_prompts
from pipez.db import Database
from pipez.engine import Engine
from pipez.events import EventBus
from pipez.llm import BRIEF_SCHEMA, INTAKE_SCHEMA, MockLLM
from pipez.prefs import PreferenceStore

_real_sleep = asyncio.sleep


async def _fast_sleep(t, *a):
    await _real_sleep(0)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_ARTIFACTS", False)
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
    db = Database(str(tmp_path / "s.sqlite3"))
    seed_prompts.seed(db)
    prefs = PreferenceStore(db)
    return db, prefs, Engine(db, EventBus(), MockLLM(), prefs)


async def wait_status(db, run_id, want, tries=6000):
    for _ in range(tries):
        if db.get_run(run_id)["status"] in want:
            return db.get_run(run_id)
        await _real_sleep(0.005)
    raise AssertionError("run stuck in %s" % db.get_run(run_id)["status"])


def sp(run):
    return run["intake_meta"]["discovery"]["standing_preferences"]


async def test_intake_stores_suggestion_only(env):
    db, prefs, engine = env
    rid = engine.submit("build a thing, always MIT")
    run = await wait_status(db, rid, ("awaiting_human",))
    assert sp(run) == [{"key": "legal.license", "value": "MIT",
                        "evidence": "always MIT"}]
    assert prefs.observations("legal.license") == []
    assert prefs.resolve("legal.license")["source"] == "none"
    assert any(r["event"] == "standing_prefs" for r in db.get_log("run:%s" % rid))


async def test_no_suggestion_without_phrase(env):
    db, prefs, engine = env
    run = await wait_status(db, engine.submit("build a thing"), ("awaiting_human",))
    assert sp(run) == []


async def test_pinned_same_value_filtered(env):
    db, prefs, engine = env
    prefs.pin("legal.license", "MIT")
    rid = engine.submit("always MIT please")
    run = await wait_status(db, rid, ("awaiting_human",))
    assert sp(run) == []


async def test_pinned_other_value_kept(env):
    db, prefs, engine = env
    prefs.pin("legal.license", "Apache-2.0")
    run = await wait_status(db, engine.submit("always MIT"), ("awaiting_human",))
    assert len(sp(run)) == 1


async def _to_open_question(db, engine, request):
    rid = engine.submit(request)
    await wait_status(db, rid, ("awaiting_human",))
    engine.answer_intake(rid, accept_all=True)
    run = await wait_status(db, rid, ("awaiting_human",))
    okey = next(d["key"] for d in run["decisions"] if d.get("free_text"))
    return rid, okey


async def test_normalised_answer_recorded(env):
    db, prefs, engine = env
    rid, okey = await _to_open_question(db, engine, "x")
    # storage.database is answered via accept_all; use a run where it is not.
    run = db.get_run(rid)
    answers = dict(run["answers"])
    answers.pop("storage.database")
    db.set_answers(rid, answers)
    engine.answer_intake(rid, {okey: "we will use sqlite"})
    await wait_status(db, rid, ("awaiting_plan",))
    obs = [o for o in prefs.observations("storage.database")
           if o["reason"] == "normalised from %s" % okey]
    assert len(obs) == 1
    assert obs[0]["scope"] == "global" and obs[0]["source"] == "intake"
    assert obs[0]["value"] == "sqlite" and obs[0]["run_id"] == rid
    assert prefs.resolve("storage.database")["source"] != "pinned"


async def test_normalised_skipped_when_key_answered(env):
    db, prefs, engine = env
    rid, okey = await _to_open_question(db, engine, "x")
    before = len(prefs.observations("storage.database"))
    engine.answer_intake(rid, {okey: "we will use sqlite"})
    await wait_status(db, rid, ("awaiting_plan",))
    mine = [o for o in prefs.observations("storage.database")
            if (o["reason"] or "").startswith("normalised")]
    assert mine == [] and len(prefs.observations("storage.database")) == before


async def test_normalised_rejected_when_from_key_not_free_text(env):
    db, prefs, engine = env
    rid, okey = await _to_open_question(db, engine, "x")
    run = db.get_run(rid)
    answers = dict(run["answers"])
    answers.pop("storage.database")
    answers[okey] = "we will use sqlite"
    db.set_answers(rid, answers)
    decisions = [dict(d, free_text=False) if d["key"] == okey else d
                 for d in run["decisions"]]
    db.set_decisions(rid, decisions)
    engine._record_normalised(db.get_run(rid), [
        {"key": "storage.database", "value": "sqlite", "from_key": okey}])
    assert prefs.observations("storage.database") == [] or all(
        not (o["reason"] or "").startswith("normalised")
        for o in prefs.observations("storage.database"))
    log = [r for r in db.get_log("run:%s" % rid) if r["event"] == "normalised_answers"]
    assert log


def test_schemas_require_new_fields():
    assert "standing_preferences" in INTAKE_SCHEMA["required"]
    assert "normalised_answers" in BRIEF_SCHEMA["required"]
    for sch, f in ((INTAKE_SCHEMA, "standing_preferences"),
                   (BRIEF_SCHEMA, "normalised_answers")):
        item = sch["properties"][f]["items"]
        assert item["additionalProperties"] is False
        assert set(item["required"]) == set(item["properties"])


def test_seeded_prompts_have_paragraphs(tmp_path):
    db = Database(str(tmp_path / "p.sqlite3"))
    seed_prompts.seed(db)
    assert "STANDING PREFERENCES" in db.get_prompt("intake_discovery")
    assert "NORMALISED ANSWERS" in db.get_prompt("intake_brief")
