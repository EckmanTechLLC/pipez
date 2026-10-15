import asyncio
import json
import re
import time

import pytest

from pipez import config, seed_prompts
from pipez.db import Database
from pipez.engine import Engine
from pipez.events import EventBus
from pipez.providers import LLMError

USAGE = {"input_tokens": 0, "output_tokens": 0, "model": "stub"}


class StubLLM:
    """Root decomposes into the given subtasks; children complete after a
    sleep. Records start/finish times per child prompt."""

    def __init__(self, subtasks, fail=(), delay=0.05):
        self.subtasks = subtasks
        self.fail = set(fail)
        self.delay = delay
        self.starts, self.ends, self.calls = {}, {}, []
        self.synth_prompts = []

    async def decide(self, system, prompt, depth, tier):
        if depth == 0:
            return ({"action": "decompose", "reasoning": "r", "shared_context": "ctx",
                     "subtasks": self.subtasks}, USAGE)
        name = re.search(r"\[depth:\d+\]\n(.*)$", prompt, re.S).group(1).strip()
        self.calls.append(name)
        self.starts[name] = time.monotonic()
        await asyncio.sleep(self.delay)
        self.ends[name] = time.monotonic()
        if name in self.fail:
            raise LLMError("boom %s" % name)
        return ({"action": "complete", "reasoning": "r",
                 "result": {"summary": name, "output": "out " + name,
                            "facts": []}}, USAGE)

    async def synthesize(self, system, prompt, tier):
        self.synth_prompts.append(prompt)
        return ({"summary": "s", "output": "o", "facts": []}, USAGE)


def sub(name, deps=None):
    d = {"role": "worker", "title": name, "prompt": name, "tier": "light"}
    if deps is not None:
        d["depends_on"] = deps
    return d


def make(tmp_path, monkeypatch, subtasks, **kw):
    monkeypatch.setattr(config, "RETRY_BASE_DELAY", 0)
    monkeypatch.setattr(config, "TASK_RETRIES", 0)
    monkeypatch.setattr(config, "ESCALATE_ON_FAILURE", False)
    monkeypatch.setattr(config, "ENABLE_ARTIFACTS", False)
    monkeypatch.setattr(config, "MAX_DEPTH", 4)
    db = Database(str(tmp_path / "t.sqlite3"))
    seed_prompts.seed(db)
    llm = StubLLM(subtasks, **kw)
    engine = Engine(db, EventBus(), llm)
    run_id = db.create_run("req")
    root = db.create_task(run_id=run_id, parent_id=None, depth=0, seq=0,
                          role="r", title="root", prompt="p", tier="deep")
    return db, llm, engine, run_id, root


def by_title(db, root):
    return {c["title"]: c for c in db.get_children(root["id"])}


async def test_chain_runs_in_order(tmp_path, monkeypatch):
    db, llm, engine, run_id, root = make(
        tmp_path, monkeypatch, [sub("A"), sub("B", [0]), sub("C", [1])])
    await engine._execute(root)
    assert llm.ends["A"] <= llm.starts["B"]
    assert llm.ends["B"] <= llm.starts["C"]
    kids = by_title(db, root)
    assert kids["B"]["depends_on"] == [kids["A"]["id"]]
    assert kids["C"]["depends_on"] == [kids["B"]["id"]]
    assert kids["A"]["depends_on"] == []
    assert db.get_task(root["id"])["status"] == "done"


async def test_diamond_overlaps(tmp_path, monkeypatch):
    db, llm, engine, run_id, root = make(
        tmp_path, monkeypatch,
        [sub("A"), sub("B", [0]), sub("C", [0]), sub("D", [1, 2])])
    await engine._execute(root)
    assert llm.ends["A"] <= min(llm.starts["B"], llm.starts["C"])
    assert llm.starts["B"] < llm.ends["C"] and llm.starts["C"] < llm.ends["B"]
    assert llm.starts["D"] >= max(llm.ends["B"], llm.ends["C"])
    # synthesizer sees the structure
    entries = json.loads(llm.synth_prompts[-1].split("# Subtask results\n")[1])
    d = next(e for e in entries if e["title"] == "D")
    assert d["depends_on"] == ["B", "C"]


async def test_failure_skips_dependents(tmp_path, monkeypatch):
    db, llm, engine, run_id, root = make(
        tmp_path, monkeypatch,
        [sub("A"), sub("B", [0]), sub("C", [1]), sub("E")], fail={"A"})
    await engine._execute(root)
    kids = by_title(db, root)
    assert kids["A"]["status"] == "failed"
    assert kids["B"]["status"] == "skipped"
    assert kids["B"]["error"] == "dependency failed: A"
    assert kids["C"]["status"] == "skipped"
    assert kids["C"]["error"] == "dependency failed: B"
    assert kids["E"]["status"] == "done"
    assert "B" not in llm.calls and "C" not in llm.calls
    assert any(r["event"] == "skipped" for r in db.get_log(kids["B"]["id"]))
    prompt = llm.synth_prompts[-1]
    failed = json.loads(prompt.split("# Failed subtasks\n")[1])
    assert {f["title"] for f in failed} == {"A", "B", "C"}
    assert db.get_task(root["id"])["status"] == "done"


async def test_all_failed_still_raises(tmp_path, monkeypatch):
    db, llm, engine, run_id, root = make(
        tmp_path, monkeypatch, [sub("A"), sub("B", [0])], fail={"A"})
    with pytest.raises(LLMError, match="all 2 subtasks failed"):
        await engine._execute(root)


async def test_invalid_edges_dropped(tmp_path, monkeypatch):
    db, llm, engine, run_id, root = make(
        tmp_path, monkeypatch,
        [sub("A", [0, 9]), sub("B", [2]), sub("C", [1]), sub("D", [0])])
    await engine._execute(root)
    kids = by_title(db, root)
    rows = [r for r in db.get_log(root["id"]) if r["event"] == "deps_invalid"]
    assert len(rows) == 1
    reasons = {d["reason"] for d in json.loads(rows[0]["detail"])["dropped"]}
    assert reasons == {"self-reference", "out of range", "cycle"}
    # cycle B<->C broken by dropping exactly one edge; D->A kept
    assert len(kids["B"]["depends_on"]) + len(kids["C"]["depends_on"]) == 1
    assert kids["D"]["depends_on"] == [kids["A"]["id"]]
    assert kids["A"]["depends_on"] == []
    assert all(k["status"] == "done" for k in kids.values())
    assert db.get_task(root["id"])["status"] == "done"


async def test_resume_reruns_failed_and_dependents(tmp_path, monkeypatch):
    db, llm, engine, run_id, root = make(
        tmp_path, monkeypatch,
        [sub("A"), sub("B", [0]), sub("C", [0]), sub("D", [1, 2])])
    await engine._run(run_id, root)
    assert db.get_run(run_id)["status"] == "done"
    kids = by_title(db, root)
    db.update_task(kids["B"]["id"], status="failed", error="x")
    llm.calls.clear()
    engine.resume(run_id)
    await asyncio.gather(*list(engine._runs))
    assert sorted(llm.calls) == ["B", "D"]
    kids = by_title(db, root)
    assert all(k["status"] == "done" for k in kids.values())
    assert db.get_run(run_id)["status"] == "done"
