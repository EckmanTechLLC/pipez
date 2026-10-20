"""Review fault-injection hook (task 28)."""
import json

from pipez import config

from tests.test_code_leaf import env, run_plan  # noqa: F401
from tests.test_deploy import dep  # noqa: F401
from tests.test_review import events, fixers


def verdicts(db, rid):
    return [json.loads(v["verdicts"]) for v in db.get_verifications(rid)]


async def test_inject_drives_fix_round(dep, monkeypatch):
    db, engine, cfg = dep
    monkeypatch.setattr(config, "VERIFY_ROUNDS", 1)
    monkeypatch.setattr(config, "REVIEW_INJECT_WRONG", "CRITERION 1")
    rid, run = await run_plan(db, engine)
    assert run["status"] == "done", run["error"]
    assert db.get_run(rid)["verification"] == "passed"
    vs = verdicts(db, rid)
    assert len(vs) == 2
    wrong = [v for v in vs[0] if v["verdict"] == "wrong"]
    assert len(wrong) == 1 and wrong[0]["injected"] is True
    assert wrong[0]["correction"].startswith("INJECTED BY PIPEZ_REVIEW_INJECT_WRONG (test hook): ")
    assert not any(v["verdict"] == "wrong" or v.get("injected") for v in vs[1])
    assert len(events(db, rid, "review_injected")) == 1
    fx = fixers(db, rid)
    assert len(fx) == 1 and "INJECTED BY" in fx[0]["prompt"]
    await engine.undeploy(rid)


async def test_inject_miss(dep, monkeypatch):
    db, engine, cfg = dep
    monkeypatch.setattr(config, "VERIFY_ROUNDS", 1)
    monkeypatch.setattr(config, "REVIEW_INJECT_WRONG", "no such claim text")
    rid, run = await run_plan(db, engine)
    assert run["status"] == "done", run["error"]
    assert db.get_run(rid)["verification"] == "passed"
    assert len(db.get_verifications(rid)) == 1
    assert len(events(db, rid, "review_inject_miss")) == 1
    assert not events(db, rid, "review_injected")
    assert not any(v.get("injected") for v in verdicts(db, rid)[0])
    await engine.undeploy(rid)


async def test_inject_empty_no_effect(dep, monkeypatch):
    db, engine, cfg = dep
    monkeypatch.setattr(config, "REVIEW_INJECT_WRONG", "")
    rid, run = await run_plan(db, engine)
    assert db.get_run(rid)["verification"] == "passed"
    assert len(db.get_verifications(rid)) == 1
    assert not events(db, rid, "review_injected") and not events(db, rid, "review_inject_miss")
    await engine.undeploy(rid)
