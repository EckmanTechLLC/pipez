# Task 08: Stop, mid-run human questions, retries, integration fix loop

## Objective
Give the human control over a running build and let the build recover from the
two most common failures without a human. (1) **Stop**: cancel a run at any
phase, keeping the sandbox for inspection. (2) **Mid-run questions**: a coding
leaf that hits a decision it must not make alone pauses the task (not the run)
as `awaiting_human`; the answer is recorded as an observation and the task
re-runs with it. (3) **Leaf retry**: a code task whose tests fail or whose
runner errors gets one more attempt with the failure tail appended. (4)
**Integration fix loop**: when `_integrate` fails, a fixer leaf runs on
`integration` with the build/test output, then integration re-runs, up to N
rounds. Plus two gaps from live testing: `tests: null` must fail when tests are
required, and the workspace needs a baseline `.gitignore`.

## Reference (read first)
- `.odin/project-index.md`; summaries for 06a (`_schedule`, `run_one`, `skipped`), 07b (`sandbox` teardown policy, `_sandboxes`), 07c (`_execute_code_task`, `.pipez/task.md`, runner stdout contract, `LEAF_REQUIRE_TESTS`, resume of failed code tasks), 07c-a (runner filter), 07d (`_integrate`, `integrate_failed`, kept sandbox), 04/04b (`awaiting_human` on runs, `free_text` decisions, `answer_intake`, observations with `scope: request`), 05 (intake card rendering)
- `pipez/engine.py` run bookkeeping: `self._runs` (asyncio tasks), `_run`, `_run_resume`; `pipez/server.py` routes; `ui/index.html` run header + intake panel; `infra/runner/pipez_runner.py` + `mock_runner.py`

## Design (binding)
- **Stop**: `POST /api/runs/{id}/stop`. Engine keeps `self._run_tasks[run_id] -> asyncio.Task`; stop = `task.cancel()` + mark every `running`/`pending`/`awaiting_children`/`synthesizing` task `cancelled` (new status; error "stopped by user") + run `cancelled` + sandbox `teardown(keep=True)` (log `sandbox_kept`) + `CancelledError` handling in `_execute*`/`_schedule` so DB state is consistent (wrap cancellation points; never leave `running` rows). `resume()` on a `cancelled` run re-executes cancelled/pending tasks like failed ones (done stay done). UI: **Stop** button on the run header while the run is active (any non-terminal status); terminal statuses: `done|failed|cancelled`.
- **Mid-run question**: runner contract gains an alternative output: the agent may write `.pipez/question.json` `{"key": "...", "question": "...", "why_it_matters": "...", "options": [{"value","label","consequences"}], "proposed_default": "..."}` instead of `result.json` (the task.md tells it: "only when the task cannot be completed without a human choice that the brief does not settle; do NOT ask about anything an engineer should decide"). The runner returns `{"ok": false, "needs_human": {...}, ...}`. Engine: task status `awaiting_human` (new task status), `task.question` stored (new JSON column), run status `awaiting_human` while any task is in that state (other tasks keep running; the DAG scheduler treats an `awaiting_human` task as not-done, so dependents wait). `POST /api/runs/{id}/tasks/{task_id}/answer` `{value, reason?}` → observation (`scope: "request"`, `source: "intake"`, `key` from the question, prefixed `task.` if it has no dot) → the task re-runs from a fresh worktree with "# Human answer" appended to task.md → run status back to `running` when no task awaits. `answer` also goes into the run's `answers` so the brief view shows it. Mock runner: `PIPEZ_MOCK_RUNNER_ASK=<task_id>` makes that task ask once, then complete on the next run. UI: question cards (reuse the intake card markup) in a "Waiting on you" block above the task table; the run status pulses like intake.
- **Leaf retry**: `PIPEZ_LEAF_RETRIES` (default 1). On runner `ok=false` (not `needs_human`) or tests failed: log `leaf_retry` with the tail, remove the worktree/branch, re-run with "# Previous attempt failed" + tail (≤ 2000 chars) appended to task.md. Only after retries are exhausted → `failed`. Cost accumulates on the task.
- **Integration fix loop**: `PIPEZ_FIX_ROUNDS` (default 2). On `integrate_failed`: create a child task `kind: "code"`, `role: "integrator-engineer"`, `title: "Fix integration (round n)"`, `module: "*"`, prompt = the failing command + tail + "make the full build and test pass; change as little as possible; do not alter public contracts"; it runs **directly on `integration`** (worktree on the `integration` branch itself, no merge step) with the leaf runner; then `_integrate` re-runs. Rounds exhausted → run `failed` as today. Fix tasks appear in the tree under the root with `depends_on` = [] and are visible in the UI like any code task.
- **Gaps**: `LEAF_REQUIRE_TESTS=1` and `tests` is null or missing → treat as failure (retry path applies). `_init_workspace`: if after scaffold no `.gitignore` exists, write a baseline (`__pycache__/ *.pyc .pytest_cache/ .mypy_cache/ .ruff_cache/ node_modules/ .venv/ dist/ build/ .pipez/`) and commit it with the scaffold.

## Deliverables
1. Stop: engine cancellation + DB consistency + route + UI button; `tests/test_stop.py` (stop during a mock leaf → run `cancelled`, no `running` rows, sandbox `kept`; resume continues).
2. Mid-run question: runner + mock runner contract, task column + statuses, scheduler wait semantics, answer route, observation, re-run; UI block; tests (ask → task `awaiting_human`, run `awaiting_human`, sibling continues, answer → task re-runs → done; observation recorded).
3. Leaf retry + `tests: null` gap; tests (first attempt fails, second succeeds → done with `leaf_retry` logged; two failures → failed).
4. Integration fix loop; tests (first `_integrate` fails on a plan with `test_command` reading a marker file the fixer creates — mock runner in fix mode writes the marker — second round passes; rounds exhausted → failed).
5. Baseline `.gitignore`; README sections for all of the above; `.odin/project-index.md`.

## Out of scope
- Live Proxmox/LLM runs (controller). Deploy/git-remote (09/10). Changing intake/plan prompts. Committing to git.

## Verification criteria
1. `.venv/bin/python -m pytest -q tests/` passes.
2. Mock smoke on **8401** (`PIPEZ_MOCK=1 PIPEZ_MOCK_RUNNER_ASK=t2`): submit → accept_all → answer open question → approve → run reaches `awaiting_human` with task t2 `awaiting_human` while t1/t3 proceed; POST the answer → run `done`; `/api/observations` has the `task.*`/question key. Then a second run with `PIPEZ_MOCK_RUNNER_ASK` unset: POST `/stop` mid-run → `cancelled`, sandbox `kept`. Stop 8401; clean up.
3. `node --check` passes; `python3 -m py_compile infra/runner/*.py` passes.

## Write summary
`.odin/sessions/task-08-control-and-fix-loops-summary.md`: files, verification output, ambiguities resolved.
