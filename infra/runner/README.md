# infra/runner - coding-leaf runners

`pipez_runner.py` runs one Claude Agent SDK session (`claude_agent_sdk.query`) in a git
worktree inside the sandbox (`/opt/pipez/venv/bin/python`). `mock_runner.py` has the
identical CLI and stdout contract, uses no SDK or network, and is what `MockSandbox` runs.
The engine uploads the right one to `<workdir>/runner.py`.

## CLI
    runner.py --cwd <worktree> --task-file .pipez/task.md --model <id>
              --max-turns N --max-budget-usd X --log <file.jsonl> [--test-command <cmd>]

`ANTHROPIC_API_KEY` is read from the environment (the engine sources `<workdir>/.env`; it is
never on a command line). The log gets one JSON line per SDK message (key redacted).

## stdout: exactly one JSON object
    {"ok", "summary", "output", "facts": [...], "files_changed": [...],   # git diff --name-only integration...HEAD
     "commit": sha|null, "tests": {"command","rc","passed","failed","tail"}|null,
     "cost_usd", "num_turns", "input_tokens", "output_tokens", "is_error", "error",
     "needs_human": {key, question, why_it_matters, options[{value,label,consequences}],
                     proposed_default}|null}

Exit 0 whenever the JSON was produced, even with `ok=false`; non-zero only if the runner
itself crashed. Missing `.pipez/result.json` gives `ok=false, error="no result.json"` (commit,
files and cost are still reported). The real runner also sets `ok=false` for uncommitted
changes or no commit, and re-runs tests to get a real `rc` and `tail`:
with `--test-command` (the plan's `test_command`, the single authority) it runs exactly that
from the worktree root and parses pytest's `N passed, M failed` line (rc-based 0/1 otherwise);
the agent's claimed command is only recorded as `tests.claimed`. Without `--test-command` it
falls back to the claim, but never executes a non-command (`none`, `n/a`, `-`, empty, "no
tests", a bare word that is not on PATH or an existing path): `tests` is then `null`.

## question.json (alternative to result.json)
Only when the task cannot be completed without a human choice the brief does not settle, the
agent writes `.pipez/question.json` `{key, question, why_it_matters, options[{value,label,
consequences}], proposed_default}` instead of `result.json`. The runner then reports
`ok=false`, `is_error=false`, `needs_human={...}`; the engine parks the task until a human
answers and re-runs it with a "# Human answer" section appended to task.md. Failed attempts are
re-run with a "# Previous attempt failed" section.

## result.json (written by the agent, not committed)
    {"summary": str, "output": str (markdown), "facts": [{"claim","value","source"}],
     "tests": {"command": str, "passed": int, "failed": int}}

`source` for code facts is a path or a test name.

## Mock runner env
- `PIPEZ_MOCK_RUNNER_FAIL=<plan task id>`: that task reports `tests.failed=1`.
- `PIPEZ_MOCK_RUNNER_CONFLICT=1`: every task writes `CONFLICT.md` with different content.
- `PIPEZ_MOCK_RUNNER_ASK=<plan task id>`: that task asks one question (key `layout`) until task.md has a "# Human answer" section.
- `PIPEZ_MOCK_RUNNER_FLAKY=<plan task id>`: fails unless task.md has a "# Previous attempt failed" section.
- `PIPEZ_MOCK_RUNNER_NOTESTS=<plan task id>`: reports `tests: null`.
- `PIPEZ_MOCK_RUNNER_FIXMARK=<file>`: fixer tasks (plan id `fix*`) write and commit that file.
- `PIPEZ_MOCK_RUNNER_SLEEP=<seconds>`: sleep before working (to stop a run mid-leaf).
