"""Deployer waits for cloud-init / apt locks and retries once on a lock failure (task 25)."""
import json

from tests.test_code_leaf import env, run_plan  # noqa: F401  (env is a fixture)
from tests.test_deploy import dep, finish  # noqa: F401  (dep is a fixture)


def events(db, rid, name):
    return [json.loads(e["detail"]) for e in db.get_log("run:" + rid) if e["event"] == name]


def order(db, rid):
    return [e["event"] for e in db.get_log("run:" + rid)
            if e["event"] in ("deploy_wait_ready", "deploy_install", "deploy_install_retry")]


async def test_wait_ready_before_first_install(dep):
    db, engine, cfg = dep
    rid, run = await run_plan(db, engine)
    d = await finish(db, rid, ("ready", "failed"))
    assert d["status"] == "ready", d["error"]
    ev = events(db, rid, "deploy_wait_ready")
    assert ev and ev[0]["seconds"] == 0 and ev[0]["timed_out"] is False
    assert order(db, rid)[0] == "deploy_wait_ready"
    await engine.undeploy(rid)


async def test_lock_failure_retried_once(dep, tmp_path):
    db, engine, cfg = dep
    marker = tmp_path / "locked"
    cfg["install_commands"] = [
        "test -f %s || { touch %s; echo 'E: Could not get lock /var/lib/apt/lists/lock' >&2; "
        "exit 100; }" % (marker, marker)]
    rid, run = await run_plan(db, engine)
    d = await finish(db, rid, ("ready", "failed"))
    assert d["status"] == "ready", d["error"]
    assert len(events(db, rid, "deploy_install_retry")) == 1
    assert len(events(db, rid, "deploy_wait_ready")) == 2
    assert [e["rc"] for e in events(db, rid, "deploy_install")] == [0]
    await engine.undeploy(rid)


async def test_other_failure_not_retried(dep):
    db, engine, cfg = dep
    cfg["install_commands"] = ["echo 'Could not get lock' >&2; exit 1"]
    rid, run = await run_plan(db, engine)
    d = await finish(db, rid, ("ready", "failed"))
    assert d["status"] == "failed" and "exited 1" in d["error"]
    assert not events(db, rid, "deploy_install_retry")
    assert len(events(db, rid, "deploy_install")) == 1
    await engine.undeploy(rid)


class _FakeSb:
    kind = "pve"
    vmid, name, ip, workdir = 1, "x", "127.0.0.1", None

    def __init__(self, ci_rc):
        self.ci_rc, self.cmds = ci_rc, []

    async def run(self, cmd, cwd=None, stdin=None, timeout=900):
        self.cmds.append(cmd)
        if "cloud-init status" in cmd:
            return {"rc": 0, "stdout": "cloud-init:%d\n" % self.ci_rc, "stderr": "", "seconds": 0}
        return {"rc": 0, "stdout": "active\n", "stderr": "", "seconds": 0}


async def test_wait_ready_shell_and_cloud_init_rc2():
    from pipez import deploy
    sb, evs, notes = _FakeSb(2), [], []
    d = deploy.Deployer(sb, "r1", {"deploy": {"env": [], "install_commands": [], "port": 1,
                        "health_path": "/"}}, "x", lambda *a, **k: None,
                        lambda n, p: evs.append((n, p)), notes.append)
    await d._wait_ready()
    assert not any("unattended" in c for c in sb.cmds)
    (name, payload), = evs
    assert name == "deploy_wait_ready"
    assert payload["cloud_init_rc"] == 2 and payload["timed_out"] is False
    assert notes and "timed out" not in notes[0]
