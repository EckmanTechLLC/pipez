# Task 12 summary

## Changes
- infra/runner/pipez_runner.py: `--test-command`; `run_tests(cwd, claimed, project_command)` runs exactly the project command, parses pytest `N passed/M failed` (rc-based 1/0 or 0/1 fallback), records the claim as `tests.claimed`. Without a project command the claim is only run if plausible (`plausible_command`: rejects none/n/a/-/empty/"no tests"/unknown bare words), else `tests` is null.
- infra/runner/mock_runner.py: accepts `--test-command`; reports it as `command` and the old claim as `claimed` (still simulated, never executes anything).
- infra/runner/README.md: CLI and test-harness behaviour documented.
- pipez/engine.py:
  - `_execute_code_task`: passes `--test-command <shell-quoted plan.test_command>`; after `git worktree add` (branch and detached) symlinks `<repo>/.venv` and `<repo>/node_modules` into the worktree (idempotent, skipped if absent/present).
  - `_task_md`: new `## Environment` section (build/test commands, `./.venv` guidance, runner re-runs test command, `tests` advisory). result.json contract unchanged.
  - New `_fail_if_code_failed`, called in `_decompose` and `_resume_task` right after `_partition`: root of a plan run with failed/skipped children raises `LLMError("N code tasks failed: a; b")` (no synthesis, run marked failed, sandbox kept by existing `ok` check). Non-plan runs unchanged.
  - `_synthesize`: for plan runs adds a "# Limits" section (cannot create/modify files; describe only what integration proves).
- tests/test_code_leaf.py: 5 new tests (placeholder claim not executed; project command authoritative + claimed recorded; engine passes flag and Environment section; venv symlink in every worktree; failed leaf fails run, sandbox kept, no resume_resynthesis, no artifacts, resume re-runs only failed+skipped leaves then integrates and ends done).

## Decisions
- Error count N includes skipped dependents (they are in `failed`), so the single-failure scenario reports 2.
- The failure check runs before the "all subtasks failed" check so plan runs get the informative message.
- Rc-based fallback when no pytest summary: rc 0 -> passed 1/failed 0; else passed 0/failed 1.

## Verification
- grep test-command in runner and engine: met. grep `ln -s` in engine: met.
- Full suite: 105 passed (the task asked >=105, including the new cases): met.
- ast.parse of pipez_runner.py: met.
- No commits, no pkill, no ports 8400/8401, no new deps: met.

## Left alone
- Retry counts, tiers, other prompts, planner, UI, deploy, PVE client, builder template. Existing resume test still passes unchanged.
