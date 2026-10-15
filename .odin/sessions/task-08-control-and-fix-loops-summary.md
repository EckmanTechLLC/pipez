# Task 08 summary: stop, mid-run questions, retries, integration fix loop

## Changed
- pipez/engine.py: `stop()` + `_mark_cancelled` (drivers tracked in `_run_tasks` via `_spawn(coro, run_id)`); `_run_with_sandbox` now wraps `_run_planned` and, on CancelledError, marks rows cancelled and keeps the sandbox; `_schedule` awaits cancelled children; `_ask_human` / `answer_task` (task + run `awaiting_human`, `_waiters` events); `_execute_code_task` rewritten as a loop (fresh worktree each pass: retry, answered question, fixer); `_integrate_with_fixes` + `IntegrationError`; `BASELINE_GITIGNORE`; task.md contract gained the `question.json` text and appended "Human answer" / "Previous attempt failed" sections; `resume()` handles `cancelled` and orphaned `awaiting_human`; `answer_intake` refuses while a task waits.
- pipez/db.py: `tasks.question` JSON column (+ migration, decode). pipez/config.py: `LEAF_RETRIES`, `FIX_ROUNDS`. pipez/sandbox.py: `_spawn` kills the child process on cancel (no orphan runners).
- pipez/server.py: POST /api/runs/{id}/stop, POST /api/runs/{id}/tasks/{task_id}/answer.
- infra/runner/pipez_runner.py: `question.json` -> `ok=false, needs_human={...}`; mock_runner.py: env ASK/FLAKY/NOTESTS/FIXMARK/SLEEP, fixer (plan id `fix*`) mode.
- ui/index.html: Stop button (non-terminal runs), resume for cancelled, `cancelled` style, "Waiting on you" cards (`renderWaiting`, `postTaskAnswer`), table no longer hidden for task-level `awaiting_human`.
- tests: new tests/test_stop.py (3), tests/test_control.py (10); tests/test_code_leaf.py `env` fixture sets FIX_ROUNDS=0, LEAF_RETRIES=0 and clears new mock env vars so older tests keep their meaning.
- README "Control and fix loops" + config row, infra/runner/README.md, .odin/project-index.md.

## Verification
1. `.venv/bin/python -m pytest -q tests/`: 93 passed. Met.
2. Mock smoke on 8401 (temp DB): ASK=t2 run reached `awaiting_human`, t2 `awaiting_human` with t1/t3 done meanwhile; POST answer -> run `done`; `/api/observations` had `task.layout=nested`. Second server start without ASK (PIPEZ_MOCK_RUNNER_SLEEP=4 to open a window): POST /stop mid-run -> `cancelled`, all 4 task rows `cancelled`, sandbox `kept`, no leftover mock_runner process. 8401 stopped via fuser; temp DB and sandbox dirs removed; 8400 untouched (still 200). Met.
3. `node --check` on the extracted inline script ok; `python3 -m py_compile infra/runner/*.py` ok. Met.
Not exercised: the UI in a browser; real Agent SDK runner (question path is py_compile-only); real PVE.

## Decisions / interpretations
- Waiting for an answer happens inside the task's coroutine (event), so the scheduler needed no change: the task is simply unfinished and dependents wait. The answer is also stored in `tasks.question.answer`, so after a server restart `answer_task` (or `resume`) re-runs the task with it.
- Fixer runs on a detached worktree at `integration` and its commit is fast-forwarded into the branch (git refuses a second worktree on the checked-out `integration`); no merge commit.
- Fixer rounds trigger on build/test failure and the dirty-tree checks only (not on git/checkout failures). Failed fixer re-raises the original integration error. Title round number = existing fixers + 1, so it continues across resumes; rounds budget is per `_integrate_with_fixes` call.
- Stop also cancels `awaiting_human` tasks (not only the four listed statuses) and refuses `verifying` runs; run stopped at plan gate resumes back to `awaiting_plan`.
- A task may ask at most 3 times (hard-coded `MAX_TASK_QUESTIONS`), then fails.
- `tests: null` with REQUIRE_TESTS now fails (retry path); merge conflicts are not retried.
- Stored question key is normalised (`task.` prefix) at ask time.
- Fixer id in task.md is `fix<seq>`; mock fixer writes `FIXMARK` file or `FIX_<id>.txt`.

## Known limits / left alone
- ff-merge of a fixer commit into a dirty `integration` checkout (build outputs not ignored) can fail with `fix_merge_failed`; no cleaning is done.
- On PVE, cancelling kills the local ssh client; a remote process may linger until the VM is destroyed.
- Failed fixer tasks stay as failed children of the root; a later resume re-runs them and skips integration while they fail.
- No git commit; live instance on 8400 untouched.
