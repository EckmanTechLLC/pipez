#!/usr/bin/env python3
"""pipez coding-leaf runner: one Claude Agent SDK session in a git worktree.

Runs inside the sandbox (stdlib + claude_agent_sdk). Streams every SDK message
as one JSON line to --log and prints ONE JSON object on stdout (see README.md).
Exit 0 whenever that JSON was produced, even with ok=false; non-zero only on a
crash of the runner itself. Credentials come from ANTHROPIC_API_KEY in the
environment and are never printed or logged.
"""
import argparse
import asyncio
import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
import time

SYSTEM_APPEND = (
    "You are a coding agent in a disposable VM, working on exactly one task in a "
    "git worktree. The task file's brief and module contract are binding. Work "
    "only inside the current directory. Commit all work on the current branch "
    "and finish by writing .pipez/result.json as the task file describes."
)
TOOLS = ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]
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


def sh(cmd, cwd, timeout=120):
    """(rc, combined output) of a shell command; never raises."""
    try:
        p = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, text=True,
                           timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "timed out after %ss" % timeout
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def redact(text):
    key = os.environ.get("ANTHROPIC_API_KEY")
    return text.replace(key, "[redacted]") if key and len(key) > 8 else text


def jsonable(msg):
    """One SDK message -> a JSON-serialisable dict (type name + its fields)."""
    if dataclasses.is_dataclass(msg) and not isinstance(msg, type):
        d = {f.name: getattr(msg, f.name) for f in dataclasses.fields(msg)}
    else:
        d = dict(getattr(msg, "__dict__", {"repr": repr(msg)}))
    d["type"] = type(msg).__name__
    return d


def git_state(cwd):
    rc, out = sh("git diff --name-only integration...HEAD", cwd)
    files = filter_paths(out.splitlines(), cwd) if rc == 0 else []
    _, head = sh("git rev-parse HEAD", cwd)
    _, base = sh("git rev-parse integration", cwd)
    head, base = head.strip(), base.strip()
    commit = head if head and head != base else None
    _, st = sh("git status --porcelain", cwd)
    paths = [l[3:].split(" -> ")[-1] for l in st.splitlines() if len(l) > 3]
    return files, commit, bool(filter_paths(paths, cwd))


def read_result(cwd):
    path = os.path.join(cwd, ".pipez", "result.json")
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def read_question(cwd):
    """The agent's alternative to result.json: .pipez/question.json, or None."""
    path = os.path.join(cwd, ".pipez", "question.json")
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not str(data.get("question") or "").strip():
        return None
    opts = [o for o in (data.get("options") or []) if isinstance(o, dict)]
    return {"key": str(data.get("key") or "").strip(),
            "question": str(data["question"]),
            "why_it_matters": str(data.get("why_it_matters") or ""),
            "options": [{"value": str(o.get("value", "")),
                         "label": str(o.get("label") or o.get("value", "")),
                         "consequences": str(o.get("consequences") or "")} for o in opts],
            "proposed_default": str(data.get("proposed_default") or "")}


NO_TESTS = {"", "none", "n/a", "na", "-", "null", "no tests", "no test", "no-tests"}


def plausible_command(cmd, cwd):
    """False for placeholders ("none", "n/a", "-", "no tests") and for a bare word
    that is neither an executable on PATH nor an existing relative path."""
    c = str(cmd or "").strip()
    if c.lower() in NO_TESTS or "no tests" in c.lower():
        return False
    if len(c.split()) > 1:
        return True
    return bool(shutil.which(c)) or os.path.exists(os.path.join(cwd, c))


def parse_summary(out):
    """(passed, failed) from pytest's summary line, or None when absent."""
    for line in reversed(out.strip().splitlines()[-15:]):
        if re.search(r"\b(passed|failed|errors?)\b", line) and re.search(r"\d+ ", line):
            n = lambda w: sum(int(x) for x in re.findall(r"(\d+) %s\b" % w, line))
            return n("passed"), n("failed") + n("error") + n("errors")
    return None


def run_tests(cwd, claimed, project_command=None):
    """Real test results, not self-reported. The plan's project command is the
    authority; the agent's claimed command is recorded as `claimed` and only
    executed (when plausible) if there is no project command."""
    claim = claimed if isinstance(claimed, dict) else {}
    claim_cmd = str(claim.get("command") or "").strip()
    project = str(project_command or "").strip()
    cmd = project or claim_cmd
    if not cmd or (not project and not plausible_command(cmd, cwd)):
        return None
    rc, out = sh("PYTHONDONTWRITEBYTECODE=1 " + cmd, cwd, timeout=600)
    parsed = parse_summary(out)
    if parsed:
        passed, failed = parsed
        if rc != 0 and failed == 0:
            failed = 1
    else:
        passed, failed = (0, 1) if rc else (1, 0)
    res = {"command": cmd, "rc": rc, "passed": passed, "failed": failed,
           "tail": redact(out[-2000:])}
    if project:
        res["claimed"] = claim_cmd or None
    return res


async def run_session(args, prompt, log):
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

    options = ClaudeAgentOptions(
        cwd=args.cwd,
        model=args.model,
        max_turns=args.max_turns,
        max_budget_usd=args.max_budget_usd,
        permission_mode="bypassPermissions",   # the VM is disposable
        allowed_tools=TOOLS,
        system_prompt={"type": "preset", "preset": "claude_code", "append": SYSTEM_APPEND},
        setting_sources=[],                     # ignore any user/project settings
    )
    final, last_text = None, ""
    async for message in query(prompt=prompt, options=options):
        log.write(redact(json.dumps(jsonable(message), default=str)) + "\n")
        log.flush()
        if isinstance(message, ResultMessage):
            final = message
        else:
            for block in getattr(message, "content", None) or []:
                text = getattr(block, "text", None)
                if text:
                    last_text = text
    return final, last_text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd", required=True)
    ap.add_argument("--task-file", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--max-turns", type=int, default=60)
    ap.add_argument("--max-budget-usd", type=float, default=5.0)
    ap.add_argument("--log", required=True)
    ap.add_argument("--test-command", default=None)
    args = ap.parse_args()
    args.cwd = os.path.abspath(args.cwd)

    out = sys.stdout
    sys.stdout = sys.stderr      # nothing but the final JSON may reach stdout
    task_path = args.task_file if os.path.isabs(args.task_file) \
        else os.path.join(args.cwd, args.task_file)
    with open(task_path) as f:
        prompt = f.read()
    os.makedirs(os.path.dirname(os.path.abspath(args.log)), exist_ok=True)

    t0 = time.time()
    final, last_text, error = None, "", None
    with open(args.log, "a") as log:
        try:
            final, last_text = asyncio.run(run_session(args, prompt, log))
        except Exception as e:  # noqa: BLE001 - reported in the JSON, not a crash
            error = redact("%s: %s" % (type(e).__name__, e))
            log.write(json.dumps({"type": "RunnerError", "error": error}) + "\n")

    usage = (getattr(final, "usage", None) or {}) if final else {}
    is_error = bool(error) or final is None or bool(getattr(final, "is_error", False))
    if error is None and final is None:
        error = "no result message from the SDK"
    elif error is None and is_error:
        error = redact("%s: %s" % (getattr(final, "subtype", "error"),
                                   getattr(final, "result", None) or last_text or ""))
    res = read_result(args.cwd) or {}
    question = None if res else read_question(args.cwd)
    tests = run_tests(args.cwd, res.get("tests"), args.test_command)   # before the dirty check
    files, commit, dirty = git_state(args.cwd)
    ok = bool(res) and not is_error
    if question:     # asking is not an error: the engine parks the task
        ok, is_error, error = False, False, "needs a human decision"
    elif not res and not error:
        error = "no result.json"
    elif ok and dirty:
        ok, error = False, "uncommitted changes left in the worktree"
    elif ok and not commit:
        ok, error = False, "no commit on the task branch"
    out_obj = {
        "ok": ok,
        "summary": str(res.get("summary") or ""),
        "output": str(res.get("output") or ""),
        "facts": res.get("facts") if isinstance(res.get("facts"), list) else [],
        "files_changed": files,
        "commit": commit,
        "tests": tests,
        "cost_usd": float(getattr(final, "total_cost_usd", None) or 0.0),
        "num_turns": int(getattr(final, "num_turns", 0) or 0),
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "is_error": is_error,
        "error": error,
        "needs_human": question,
        "seconds": round(time.time() - t0, 1),
    }
    out.write(json.dumps(out_obj) + "\n")
    out.flush()


if __name__ == "__main__":
    main()
