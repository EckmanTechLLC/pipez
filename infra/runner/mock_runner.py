#!/usr/bin/env python3
"""Mock coding-leaf runner: same CLI and stdout contract as pipez_runner.py,
no SDK and no network. Writes <module>/<plan_id>.py plus a trivial test,
commits, writes .pipez/result.json.

Env: PIPEZ_MOCK_RUNNER_FAIL=<plan_id> reports tests.failed=1 for that task;
PIPEZ_MOCK_RUNNER_CONFLICT=1 makes every task write the same CONFLICT.md with
different content (merge-conflict test); =once only on the first attempt (a task.md
with a "# Previous attempt failed" section writes none, so the conflict retry succeeds).
PIPEZ_MOCK_RUNNER_ASK=<plan_id>: that task writes .pipez/question.json and
reports needs_human (once: a task.md with a "# Human answer" section completes).
PIPEZ_MOCK_RUNNER_FLAKY=<plan_id>: that task fails (tests.failed=1) unless task.md
has a "# Previous attempt failed" section (retry test).
PIPEZ_MOCK_RUNNER_FIXMARK=<file>: a fixer task (plan id fix*) writes that file.
PIPEZ_MOCK_RUNNER_NOTESTS=<plan_id>: that task reports tests: null.
PIPEZ_MOCK_RUNNER_SLEEP=<seconds>: sleep before working (stop test).
--review: writes .pipez/verdicts.json (two confirmed verdicts, or one `wrong` when the
task file contains MOCK_WRONG), commits nothing, runs no tests, and reports
ok=false "reviewer modified the worktree" when the worktree is dirty.
"""
import time
import argparse
import json
import os
import re
import subprocess
import sys

IGNORED = ("__pycache__/", ".pyc", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/",
           "node_modules/", ".pipez/", ".venv/", "dist/", "build/")


def is_ignored(path):
    """True for build artefacts / runner scratch paths (directory entries match on
    a whole path component; ".pyc" matches the suffix)."""
    p = "/" + path.strip().strip('"')
    if p.rsplit("/", 1)[-1].split(" ")[-1] in (".venv", "node_modules"):
        return True     # a symlinked .venv / node_modules shows up without a slash
    for pat in IGNORED:
        if pat.endswith("/"):
            if ("/" + pat) in p:
                return True
        elif p.endswith(pat):
            return True
    return False


def filter_paths(paths, cwd=None):
    """Drop IGNORED paths, then (if cwd given) anything git check-ignore reports."""
    kept = [p for p in paths if p.strip() and not is_ignored(p)]
    if cwd and kept:
        try:
            r = subprocess.run(["git", "check-ignore", "--stdin", "-z"], cwd=cwd,
                               input="\0".join(kept) + "\0", capture_output=True,
                               text=True, timeout=60)
            ign = set(x for x in r.stdout.split("\0") if x)
            kept = [p for p in kept if p not in ign]
        except Exception:  # noqa: BLE001 - filter is best effort
            pass
    return kept


def sh(cmd, cwd):
    p = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def field(text, label, default):
    m = re.search(r"^%s:\s*(.+?)\s*$" % re.escape(label), text, re.M)
    return m.group(1) if m else default


def review(a, cwd, text):
    wrong = "MOCK_WRONG" in text
    verdicts = ([{"claim": "criterion 1", "verdict": "wrong", "note": "mock: does not hold",
                  "correction": "mock correction"}] if wrong else
                [{"claim": "criterion %d" % i, "verdict": "confirmed", "note": "mock ok",
                  "correction": ""} for i in (1, 2)])
    os.makedirs(os.path.join(cwd, ".pipez"), exist_ok=True)
    data = {"summary": "mock review: %s" % ("1 wrong" if wrong else "all confirmed"),
            "ok": not wrong, "verdicts": verdicts}
    with open(os.path.join(cwd, ".pipez", "verdicts.json"), "w") as f:
        json.dump(data, f)
    _, st = sh("git status --porcelain", cwd)
    dirty = filter_paths([l[3:].split(" -> ")[-1] for l in st.splitlines() if len(l) > 3], cwd)
    os.makedirs(os.path.dirname(os.path.abspath(a.log)), exist_ok=True)
    with open(a.log, "a") as f:
        f.write(json.dumps({"type": "MockRunner", "review": True}) + "\n")
    sys.stdout.write(json.dumps({
        "ok": not dirty, "summary": data["summary"], "output": "", "facts": [],
        "files_changed": [], "commit": None, "tests": None, "verdicts": data,
        "cost_usd": 0.01, "num_turns": 1, "input_tokens": 10, "output_tokens": 5,
        "is_error": False,
        "error": "reviewer modified the worktree: %s" % ", ".join(dirty) if dirty else None,
    }) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd", required=True)
    ap.add_argument("--task-file", required=True)
    ap.add_argument("--model", default="mock")
    ap.add_argument("--max-turns", type=int, default=60)
    ap.add_argument("--max-budget-usd", type=float, default=5.0)
    ap.add_argument("--log", required=True)
    ap.add_argument("--test-command", default=None)
    ap.add_argument("--review", action="store_true")
    a = ap.parse_args()
    cwd = os.path.abspath(a.cwd)
    try:        # .pipez/runner.json: settings the engine keeps off argv
        with open(os.path.join(cwd, ".pipez", "runner.json")) as f:
            settings = json.load(f)
    except (OSError, ValueError):
        settings = {}
    if not isinstance(settings, dict):
        settings = {}
    if a.test_command is None and settings.get("test_command"):
        a.test_command = str(settings["test_command"])
    a.review = a.review or bool(settings.get("review"))
    tf = a.task_file if os.path.isabs(a.task_file) else os.path.join(cwd, a.task_file)
    text = open(tf).read()
    plan_id = field(text, "Plan task id", "task")
    module = re.sub(r"[^A-Za-z0-9_]", "_", field(text, "Module", "mod")) or "mod"
    if os.environ.get("PIPEZ_MOCK_RUNNER_SLEEP"):
        time.sleep(float(os.environ["PIPEZ_MOCK_RUNNER_SLEEP"]))
    if a.review:
        return review(a, cwd, text)
    fail = os.environ.get("PIPEZ_MOCK_RUNNER_FAIL") == plan_id
    if os.environ.get("PIPEZ_MOCK_RUNNER_FLAKY") == plan_id \
            and not re.search(r"^# Previous attempt failed$", text, re.M):
        fail = True
    if os.environ.get("PIPEZ_MOCK_RUNNER_ASK") == plan_id \
            and not re.search(r"^# Human answer$", text, re.M):
        os.makedirs(os.path.join(cwd, ".pipez"), exist_ok=True)
        question = {"key": "layout", "question": "Which layout should %s use?" % plan_id,
                    "why_it_matters": "The brief does not settle it.",
                    "options": [{"value": "flat", "label": "flat", "consequences": "mock: flat"},
                                {"value": "nested", "label": "nested", "consequences": "mock: nested"}],
                    "proposed_default": "flat"}
        with open(os.path.join(cwd, ".pipez", "question.json"), "w") as f:
            json.dump(question, f)
        sys.stdout.write(json.dumps({
            "ok": False, "needs_human": question, "summary": "", "output": "",
            "facts": [], "files_changed": [], "commit": None, "tests": None,
            "cost_usd": 0.0, "num_turns": 1, "input_tokens": 1, "output_tokens": 1,
            "is_error": False, "error": "needs a human decision"}) + "\n")
        return

    files = []
    if plan_id.startswith("fix"):
        mark = os.environ.get("PIPEZ_MOCK_RUNNER_FIXMARK") or "FIX_%s.txt" % plan_id
        with open(os.path.join(cwd, mark), "w") as f:
            f.write("fixed by %s\n" % plan_id)
        files.append(mark)
        module = None
    else:
        os.makedirs(os.path.join(cwd, module), exist_ok=True)
    conflict = os.environ.get("PIPEZ_MOCK_RUNNER_CONFLICT")
    if conflict == "once" and not re.search(r"^# Previous attempt failed$", text, re.M):
        conflict = "1"      # "once": only the first attempt writes CONFLICT.md
    if conflict == "1":
        with open(os.path.join(cwd, "CONFLICT.md"), "w") as f:
            f.write("written by %s\n" % plan_id)
        files.append("CONFLICT.md")
    py = tp = None
    if module:
        py = "%s/%s.py" % (module, plan_id)
        with open(os.path.join(cwd, py), "w") as f:
            f.write('"""Mock implementation for task %s (module %s)."""\n\n\n'
                    'def value():\n    return "%s"\n' % (plan_id, module, plan_id))
        tp = "%s/test_%s.py" % (module, plan_id)
        with open(os.path.join(cwd, tp), "w") as f:
            f.write("def test_%s():\n    assert True\n" % re.sub(r"\W", "_", plan_id))
        files += [py, tp]
    cmd = "python3 -m pytest -q"
    passed, failed = (0, 1) if fail else (1, 0)
    os.makedirs(os.path.join(cwd, ".pipez"), exist_ok=True)
    result = {"summary": "mock built %s" % plan_id,
              "output": "Mock output for %s. Run tests with `%s`." % (plan_id, cmd),
              "facts": [{"claim": "%s has a trivial value()" % plan_id,
                         "value": plan_id, "source": py or files[0]}],
              "tests": {"command": cmd, "passed": passed, "failed": failed}}
    with open(os.path.join(cwd, ".pipez", "result.json"), "w") as f:
        json.dump(result, f)
    sh("git add -A && git commit -q -m %s" % json.dumps("mock: implement %s" % plan_id), cwd)

    _, head = sh("git rev-parse HEAD", cwd)
    _, changed = sh("git diff --name-only integration...HEAD", cwd)
    os.makedirs(os.path.dirname(os.path.abspath(a.log)), exist_ok=True)
    with open(a.log, "a") as f:
        f.write(json.dumps({"type": "MockRunner", "plan_id": plan_id}) + "\n")
    out = {
        "ok": True, "summary": result["summary"], "output": result["output"],
        "facts": result["facts"],
        "files_changed": filter_paths(changed.splitlines(), cwd),
        "commit": head.strip() or None,
        "tests": None if os.environ.get("PIPEZ_MOCK_RUNNER_NOTESTS") == plan_id else dict({
            "command": a.test_command or cmd, "rc": 1 if fail else 0, "passed": passed,
            "failed": failed, "tail": "1 failed (mock)" if fail else "1 passed (mock)"},
            **({"claimed": cmd} if a.test_command else {})),
        "cost_usd": 0.01, "num_turns": 1, "input_tokens": 10, "output_tokens": 5,
        "is_error": False, "error": None,
    }
    sys.stdout.write(json.dumps(out) + "\n")


if __name__ == "__main__":
    main()
