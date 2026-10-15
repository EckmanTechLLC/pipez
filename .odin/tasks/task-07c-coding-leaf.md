# Task 07c: Coding leaves — Agent SDK runner in the sandbox, worktrees, merge

## Objective
Replace the `code_task_placeholder` path: a `kind == "code"` task now runs a
Claude Agent SDK session inside the run's sandbox VM, in its own git worktree,
against its module contract and acceptance list, and must leave a committed
branch plus a machine-readable result. Completed branches are merged into an
`integration` branch under a lock; dependents branch from `integration` so they
see their dependencies' code. Merge conflicts and failing tests fail the task
(dependents then skip, per 06a). Everything runs offline in mock mode with a
mock runner and real git, so tests are hermetic. The integrator root's
build/test step, repo artifact, and UI are task 07d.

## Reference (read first)
- `.odin/project-index.md`; summaries for 06a (scheduler, `run_one(child, dep_values)`), 06b (`_materialise_plan`, task `kind/module/acceptance`, placeholder), 07b + 07b-a (`Sandbox.run/put/get`, secret env → `/work/<run>/.env`, `MockSandbox` semantics, provisioning in the engine), 07a (`/opt/pipez/venv` has `claude_agent_sdk`; `claude` CLI on PATH; user `etl`; `cpu: host` needed)
- Claude Agent SDK (Python) — **read the official docs before writing the runner**; do not guess the API: `https://code.claude.com/docs/en/agent-sdk/python` (and `.../agent-sdk/overview`). You need: `query()` (or the client class), `ClaudeAgentOptions` fields for `cwd`, `permission_mode` (use the bypass mode — the VM is disposable), `model`, `max_turns`, `allowed_tools`, `system_prompt`; the result message type carrying `total_cost_usd`, `num_turns`, `is_error`, `result` text, and usage. Authentication is `ANTHROPIC_API_KEY` in the process environment.
- `pipez/engine.py` `_execute` (placeholder branch), `_decompose`/`_schedule` (`run_one(child, dep_values)` — `dep_values` are the dependencies' results)

## Design (binding)
- **Workspace** (created right after provisioning for runs with a plan; `engine._init_workspace(run)`): `/work/<run_id>/repo` — `git init -b main`, `git config user.name pipez / user.email pipez@local`; run each `scaffold_commands` entry with `bash -lc` in the repo, logging `scaffold` rows (`cmd, rc, seconds, tail`); any non-zero rc → run `failed` with `scaffold_failed` before any leaf starts; commit `scaffold`; `git branch integration`. Also `put` the runner to `/work/<run_id>/runner.py` and write `/work/<run_id>/.env` with `ANTHROPIC_API_KEY` (via the sandbox's secret-env path; never on a command line).
- **Leaf** (`engine._execute_code_task(task, dep_values)`): under the merge lock, `git worktree add /work/<run>/wt/<task_id> -b task/<task_id> integration` (so it includes everything merged so far). Write `.pipez/task.md` in the worktree: the brief (binding), `architecture` and `repo_layout` from the plan, this module's `contract`, the task `prompt`, `acceptance` as a checklist, one-paragraph summaries of each dependency's result, and the runner contract (work only inside this worktree; keep the public interface in the contract; add/keep tests runnable with the repo's standard command; commit all work on this branch with a meaningful message; finally write `.pipez/result.json` — see shape). Then `sandbox.run("set -a; . /work/<run>/.env; set +a; /opt/pipez/venv/bin/python /work/<run>/runner.py --cwd <wt> --task-file .pipez/task.md --model <tier model> --max-turns $PIPEZ_LEAF_MAX_TURNS --max-budget-usd $PIPEZ_LEAF_MAX_BUDGET_USD --log /work/<run>/logs/<task_id>.jsonl", timeout=PIPEZ_LEAF_TIMEOUT)`.
- **Runner** `infra/runner/pipez_runner.py` (plain Python, stdlib + `claude_agent_sdk`): parses args; builds the prompt from the task file; runs the SDK session streaming every message as one JSON line to `--log`; on finish reads `<cwd>/.pipez/result.json` if present; emits ONE JSON object on stdout: `{ "ok": bool, "summary", "output", "facts": [...], "files_changed": [...] (from `git diff --name-only integration...HEAD` run by the runner), "commit": sha or null, "tests": {"command", "rc", "passed", "failed", "tail"} | null, "cost_usd", "num_turns", "input_tokens", "output_tokens", "is_error", "error" }`. If `result.json` is missing, `ok=false, error="no result.json"` but still report commit/files/cost. The runner never prints secrets. Exit code 0 when it produced the JSON (even if `ok=false`); non-zero only on its own crash.
- **Result shape in result.json** (what the agent is told to write): `{"summary": str, "output": str (markdown: what was built, how to run tests), "facts": [{claim, value, source}], "tests": {"command": str, "passed": int, "failed": int}}`. `source` for code facts is a path or a test name.
- **Mock runner** `infra/runner/mock_runner.py`: same CLI and stdout contract; creates `<module>/<task_id>.py` with a docstring, a trivial test file, commits, writes `result.json` with `tests.passed=1`. Env `PIPEZ_MOCK_RUNNER_FAIL=<task_id>` makes that task report `tests.failed=1`; `PIPEZ_MOCK_RUNNER_CONFLICT=1` makes every task write the same `CONFLICT.md` with different content (for the merge-conflict test). `MockSandbox` runs use the mock runner path and the host's `python3`/git.
- **Policy after the runner returns**: `ok=false` or `is_error` → task `failed` (error = runner error / last text). `tests.failed > 0` and `config.LEAF_REQUIRE_TESTS` → `failed` with the test tail. Otherwise under the lock: `git -C repo merge --no-ff task/<id> -m "merge <id>: <title>"` into `integration`; conflict → `git merge --abort`, task `failed` with `merge_conflict` log listing files. Success → task `done`; result = `{summary, output, facts}` + `metadata` (`files_changed, commit, tests, cost_usd, num_turns`), tokens added via `db.add_tokens`; new column `tasks.cost_usd REAL` accumulated.
- **Config**: `PIPEZ_LEAF_MAX_TURNS` (60), `PIPEZ_LEAF_MAX_BUDGET_USD` (5.0), `PIPEZ_LEAF_TIMEOUT` (1800 s), `PIPEZ_LEAF_REQUIRE_TESTS` (1).
- **Decompose decisions inside code tasks** are not allowed: the runner is the whole execution; no `_decide` call for `kind == "code"`.
- **Resume**: a failed code task re-runs from a fresh worktree (`git worktree remove --force`, delete the branch) off the current `integration`.

## Deliverables
1. `infra/runner/pipez_runner.py` (Agent SDK) and `infra/runner/mock_runner.py` (contract-identical), both `python3 -m py_compile` clean; `infra/runner/README.md` (CLI, stdout contract, result.json contract).
2. `engine.py`: `_init_workspace`, `_execute_code_task`, merge lock + policy, resume handling; config keys; `tasks.cost_usd` migration; `db.add_cost`.
3. `sandbox.py`: a `git(args, cwd)` helper on `Sandbox` (thin over `run`) and `MockSandbox` runner-path selection.
4. Tests `tests/test_code_leaf.py` (mock sandbox, real git in temp dir): happy path — 3 plan tasks run, each lands a commit on `integration`, dependents' worktrees contain their dependency's file; `PIPEZ_MOCK_RUNNER_FAIL=t2` → `t2` failed with test tail, its dependents `skipped`, unrelated tasks done; conflict mode → second task `failed` with `merge_conflict` and `integration` still clean (`git status --porcelain` empty); resume after a failure re-runs only the failed task and its dependents; scaffold failure → run failed before any leaf; secrets never appear in any composed command (assert on the sandbox's recorded commands).
5. README "Code tasks" section; `.odin/project-index.md`.

## Out of scope
- Integrator build/test, repo artifact export, UI changes (07d). Live run against Proxmox/Anthropic (controller does it). Windows. Committing to git.

## Verification criteria
1. `.venv/bin/python -m pytest -q tests/` passes.
2. Mock server smoke on **8401** (`PIPEZ_MOCK=1`): submit → accept_all (answer the open question if 04b has landed) → approve → `done`; `/api/runs/{id}/tasks` shows the 3 code children `done` with `cost_usd` present and `metadata.files_changed` non-empty; the mock workspace's `integration` branch has 3 merge commits (print `git log --oneline integration` from the mock sandbox dir before teardown — add a debug env `PIPEZ_SANDBOX_KEEP=1` that keeps mock dirs). Stop 8401, clean up.
3. `python3 -m py_compile infra/runner/*.py` passes; `grep -n "ANTHROPIC_API_KEY" pipez/engine.py pipez/sandbox.py` shows it only flows through the secret-env path.

## Write summary
`.odin/sessions/task-07c-coding-leaf-summary.md`: files, verification output, the exact SDK calls used (from the docs you read, with the URL), ambiguities resolved.
