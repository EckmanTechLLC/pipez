"""Deploy failure inside a plan run (task 20): run flagged, sandbox kept, redeploy + reverify."""
import json
import shutil

import pytest

from pipez import config, deploy

from tests.test_code_leaf import env, run_plan  # noqa: F401  (env is a fixture)
from tests.test_deploy import dep, finish  # noqa: F401  (dep is a fixture)
from tests.test_intake import _real_sleep


def events(db, rid, name):
    return [json.loads(e["detail"]) for e in db.get_log("run:" + rid) if e["event"] == name]


async def settle(db, rid):
    for _ in range(3000):
        if db.get_run(rid)["status"] == "done":
            return
        await _real_sleep(0.01)
    raise AssertionError("run stuck: %s" % db.get_run(rid)["status"])


@pytest.fixture
def flaky(dep, tmp_path):
    """Deploy whose install command fails until the marker file exists."""
    db, engine, cfg = dep
    marker = tmp_path / "ok"
    cfg["install_commands"] = ["test -f %s || { echo nope >&2; false; }" % marker]
    return db, engine, cfg, marker


async def test_failed_deploy_flags_run_keeps_sandbox(flaky):
    db, engine, cfg, marker = flaky
    rid, run = await run_plan(db, engine)
    assert run["status"] == "done", run["error"]
    assert db.get_deployment(rid)["status"] == "failed"
    assert db.get_run(rid)["verification"] == "error"
    assert db.get_sandbox(rid)["status"] == "kept"
    ev = events(db, rid, "review_error")
    assert ev and ev[0]["reason"].startswith("deployment failed:") and "nope" in ev[0]["reason"]


async def test_failed_deploy_with_verify_auto_off_is_skipped(flaky, monkeypatch):
    db, engine, cfg, marker = flaky
    monkeypatch.setattr(config, "VERIFY_AUTO", False)
    rid, run = await run_plan(db, engine)
    assert run["status"] == "done", run["error"]
    assert db.get_run(rid)["verification"] == "skipped"
    assert not events(db, rid, "review_error")
    assert db.get_sandbox(rid)["status"] == "destroyed"


async def test_manual_deploy_then_reverify(flaky):
    db, engine, cfg, marker = flaky
    rid, run = await run_plan(db, engine)
    assert db.get_run(rid)["verification"] == "error"
    marker.write_text("1")
    engine.deploy(rid)
    await settle(db, rid)
    d = db.get_deployment(rid)
    assert d["status"] == "ready", d["error"]
    assert (await engine.reverify(rid))["status"] == "verifying"
    for _ in range(3000):
        if db.get_run(rid)["status"] == "done":
            break
        await _real_sleep(0.01)
    assert db.get_run(rid)["status"] == "done"
    assert db.get_run(rid)["verification"] == "passed"
    assert db.get_sandbox(rid)["status"] == "destroyed"
    await engine.undeploy(rid)


async def test_reverify_refuses_gone_sandbox(flaky):
    db, engine, cfg, marker = flaky
    rid, run = await run_plan(db, engine)
    marker.write_text("1")
    engine.deploy(rid)
    await settle(db, rid)
    assert db.get_deployment(rid)["status"] == "ready"
    engine._sandboxes.pop(rid, None)
    shutil.rmtree(db.get_sandbox(rid)["name"], ignore_errors=True)
    with pytest.raises(ValueError, match="sandbox is gone"):
        await engine.reverify(rid)
    await engine.undeploy(rid)


def test_make_deploy_sandbox_uses_deploy_boot_timeout(monkeypatch):
    monkeypatch.setattr(config, "MOCK", False)
    monkeypatch.setattr(config, "SANDBOX", "pve")
    monkeypatch.setattr(config, "DEPLOY_BOOT_TIMEOUT", 123.0)
    sb = deploy.make_deploy_sandbox("abc", client=object())
    assert sb.boot_timeout == 123.0
