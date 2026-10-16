"""Task 14: runner argv hygiene, lost-runner diagnosis, pytest rc 5, conflict retry."""
import json
import os
import re
import sys

import pytest

from pipez import config
from tests.test_code_leaf import RecSandbox, by_plan_id, env, run_plan  # noqa: F401

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "infra", "runner"))
import pipez_runner  # noqa: E402


async def test_runner_json_written_and_argv_clean(env):
    db, engine, llm, _ = env
    rid, run = await run_plan(db, engine)
    assert run["status"] == "done", run["error"]
    lines = [l for l in RecSandbox.lines if "runner.py" in l]
    assert lines and not any("pytest" in l or "--test-command" in l for l in lines)
    wd = db.get_sandbox(rid)["name"]
    t1 = by_plan_id(db, rid)["t1"]
    cfg = json.load(open(os.path.join(wd, "wt", t1["id"], ".pipez", "runner.json")))
    assert cfg == {"test_command": "python3 -m pytest -q", "review": False}
    tests = json.loads(t1["result"])["metadata"]["tests"]
    assert tests["command"] == "python3 -m pytest -q"      # runner read the json


class KillSandbox(RecSandbox):
    """First runner call 'loses the connection' (rc 255) after an agent pkill."""
    fired = False

    async def _exec(self, line, stdin, timeout):
        if "runner.py" in line and not KillSandbox.fired:
            KillSandbox.fired = True
            log = re.search(r"--log (\S+)", line).group(1).strip("'")
            os.makedirs(os.path.dirname(log), exist_ok=True)
            block = "ToolUseBlock(id='x', name='Bash', input={'command': 'pkill -f pytest', " \
                    "'description': 'kill hung tests'})"
            with open(log, "w") as f:
                f.write("x" * 5000 + "\n")
                f.write(json.dumps({"type": "AssistantMessage", "content": [block]}) + "\n")
            RecSandbox.lines.append(line)
            return 255, b"", b""
        return await super()._exec(line, stdin, timeout)


async def test_lost_runner_names_broad_kill(env, monkeypatch):
    db, engine, llm, _ = env
    monkeypatch.setattr(config, "LEAF_RETRIES", 1)
    KillSandbox.fired = False
    engine._sandbox_factory = KillSandbox
    rid, run = await run_plan(db, engine)
    assert run["status"] == "done", [t["error"] for t in by_plan_id(db, rid).values()]
    retried = [t for t in by_plan_id(db, rid).values()
               if any(e["event"] == "leaf_retry" for e in db.get_log(t["id"]))]
    assert len(retried) == 1
    ev = next(e for e in db.get_log(retried[0]["id"]) if e["event"] == "leaf_retry")
    d = json.loads(ev["detail"])
    assert "runner connection lost (rc=255)" in d["error"] or "rc=255" in d["tail"]
    assert "pkill -f pytest" in d["tail"] and "a broad kill terminated the runner" in d["tail"]
    wd = db.get_sandbox(rid)["name"]
    md = open(os.path.join(wd, "wt", retried[0]["id"], ".pipez", "task.md")).read()
    assert "a broad kill terminated the runner" in md


def test_bash_commands_dict_and_no_log():
    line = json.dumps({"content": [{"name": "Bash", "input": {"command": "ls"}}]})
    from pipez.engine import Engine
    assert Engine._bash_commands("cut-off junk\n" + line) == ["ls"]
    assert Engine._bash_commands("") == []


def test_pytest_rc5_is_no_tests(tmp_path):
    res = pipez_runner.run_tests(str(tmp_path), None, "echo 'no tests ran in 0.01s'; exit 5")
    assert (res["passed"], res["failed"], res["rc"]) == (0, 0, 5)
    assert res["note"] == "no tests collected"
    bad = pipez_runner.run_tests(str(tmp_path), None, "exit 1")
    assert bad["failed"] == 1


def test_system_append_forbids_broad_kills():
    assert "pkill" in pipez_runner.SYSTEM_APPEND


async def test_conflict_retry_on_fresh_worktree(env, monkeypatch):
    db, engine, llm, _ = env
    monkeypatch.setenv("PIPEZ_MOCK_RUNNER_CONFLICT", "once")
    rid, run = await run_plan(db, engine)
    assert run["status"] == "done", run["error"]
    ts = by_plan_id(db, rid)
    assert all(t["status"] == "done" for t in ts.values())
    hit = [t for t in ts.values()
           if any(e["event"] == "merge_conflict" for e in db.get_log(t["id"]))]
    assert hit
    t = hit[0]
    assert sum(1 for e in db.get_log(t["id"]) if e["event"] == "worktree") == 2
    md = open(os.path.join(db.get_sandbox(rid)["name"], "wt", t["id"], ".pipez", "task.md")).read()
    assert "conflicted with work merged by a sibling task on: CONFLICT.md" in md
