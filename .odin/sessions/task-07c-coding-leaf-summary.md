# Task 07c summary: coding leaves

## Files
- infra/runner/pipez_runner.py (Agent SDK runner), mock_runner.py (same CLI/stdout contract), README.md.
- pipez/engine.py: `_init_workspace`, `_execute_code_task`, `_task_md`, `_leaf_model`, `_merge_lock`, `ScaffoldError`; `_execute(task, dep_values)` routes code tasks (no `_decide`); `_run_with_sandbox` inits the workspace (scaffold failure -> run failed `scaffold_failed...`, sandbox kept) and now keeps the sandbox if any task failed/skipped.
- pipez/sandbox.py: `Sandbox.git(args, cwd)`, `runner_script`/`python` per kind (mock: mock_runner.py + python3), `PIPEZ_SANDBOX_KEEP` honoured in `MockSandbox.teardown`.
- pipez/db.py: `tasks.cost_usd` migration, `add_cost`. pipez/config.py: `ANTHROPIC_API_KEY`, `LEAF_MAX_TURNS/MAX_BUDGET_USD/TIMEOUT/REQUIRE_TESTS`, `SANDBOX_KEEP`.
- tests/test_code_leaf.py (6 tests). README (config rows, "Code tasks"), .odin/project-index.md.
- Small edits to existing files: tests/test_intake.py `wait_status` default tries 500 -> 6000 (real git subprocesses); tests/test_plan.py placeholder assertion -> `runner` event, FailingLLM also fails `synthesize` (code tasks no longer call decide); pipez/llm.py MockLLM scaffold `python -m venv .venv` -> `mkdir -p tests` (venv cost 6 s per run).

## Agent SDK calls used (docs: https://code.claude.com/docs/en/agent-sdk/python and https://code.claude.com/docs/en/agent-sdk/overview)
- `from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query`; `async for message in query(prompt=str, options=ClaudeAgentOptions(...))`.
- Options: cwd, model, max_turns, max_budget_usd, permission_mode="bypassPermissions", allowed_tools=[Read, Write, Edit, Bash, Glob, Grep], system_prompt={"type":"preset","preset":"claude_code","append":...}, setting_sources=[].
- Result: `ResultMessage` fields `is_error`, `subtype`, `result`, `num_turns`, `total_cost_usd`, `usage` (input_tokens/output_tokens). Auth: ANTHROPIC_API_KEY env.
- Not executed: claude_agent_sdk is not installed locally (only py_compile checked); the SDK path has never run, only the docs-derived calls. Cache tokens are not added to input_tokens.

## Verification
1. pytest tests/: 72 passed. Met.
2. Mock smoke on 8401 (PIPEZ_MOCK=1, PIPEZ_SANDBOX_KEEP=1, temp DB): submit, accept_all, answered the open question, approve -> done; 3 code tasks done, cost_usd 0.01 each, metadata.files_changed non-empty. `git log --oneline integration`: 3 merge commits (API endpoint, Core logic, Core data model) over the leaf commits and `scaffold`. 8401 stopped, temp files and mock dirs removed; 8400 untouched (still 200). Met.
3. py_compile infra/runner/*.py ok. grep ANTHROPIC_API_KEY: engine.py only in `env={...}` passed to `sandbox.run` (secret-env path) and the config check; sandbox.py none. Test asserts the key value never appears in any composed command and is in `.env` mode 600. Met.

## Decisions / interpretations
- Paths use `sandbox.workdir` (== /work/<run_id> on PVE; temp dir on mock). API key flows through `sandbox.run(env=...)` rather than a literal `. .env` in the command; compose_command emits the sourcing.
- `--max-turns`/`--max-budget-usd` interpolated as numbers, not shell vars.
- Plan task ids (t1..) are not stored in the DB; the plan id is taken from `plan.tasks[task.seq]` and written to task.md ("Plan task id"), which the mock runner uses for FAIL matching and file names. Branches/worktrees use the DB task id.
- Dependency summaries are read from the DB (`depends_on`), since resume passes (result, changed) tuples as dep_values.
- Runner semaphore: leaf runs share the engine semaphore (MAX_CONCURRENCY).
- Real runner extras beyond the spec: ok=false on uncommitted changes or no commit; it re-runs the claimed test command for a real rc/tail (failed>=1 if rc != 0).
- `.venv/`, `node_modules/`, `__pycache__/`, `.pipez/` excluded via .git/info/exclude (not committed).
- Non-anthropic tier (local) falls back to the standard model for leaves.
- Sandbox kept when the run ended with failed tasks (needed so resume finds the repo); previously a "done" run with failed children destroyed it.
- Worktrees are not removed after merge.

## Not done / left alone
- Integrator build/test, repo export, UI (07d). No live Anthropic/Proxmox. No git commit. If REQUIRE_TESTS is on and a runner reports `tests: null`, the task still passes (spec only fails on failed > 0).
