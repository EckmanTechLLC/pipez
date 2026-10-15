# Task 12 — Leaf test harness: authoritative test command, venv in worktrees, failed leaves fail the run

## Objective
The first real build (run c6973d74ac0e on the VM instance) lost 2 of 7 code leaves and then
reported `done` with nothing integrated. Three engine defects, all ours:
(1) the in-VM runner shell-executes whatever test command the agent *claims* (`"none"` → rc 127
→ "1 failed"; `python3 -m pytest` → system python without pytest); (2) leaf worktrees have no
`.venv` (scaffold builds `repo/.venv`, gitignored; worktrees start empty) and the leaf prompt
never names the test command or interpreter; (3) when children fail, the root skips
`_integrate` but still synthesises and `finish_run(..., "done")` — the LLM then fabricates
"I rebuilt those files". Make the plan's `test_command` the single authority, give every
worktree the venv, and make failed code leaves fail the run (sandbox kept, resume re-runs
only the failed leaves, then integrates and deploys).

## Reference
- `pipez/engine.py`
  - `_execute_code_task` ~1483–1600: worktree add (~1519–1530), runner cmd (~1534),
    result/tests checks (~1560–1590), leaf retry.
  - leaf prompt builder (the `## Runner contract` f-string) ~1430–1480.
  - parent paths: normal `_execute` parent branch ~1655–1690 (`_partition`, integrate,
    synthesize) and resume path `_resume_task` ~1140–1185. `_run` ~688, `_run_resume` ~1118,
    `_run_with_sandbox` ~1095.
  - `_synthesize` ~1770–1800 (prompt assembly; `# Failed subtasks` section).
- `infra/runner/pipez_runner.py` — `run_tests(cwd, claimed)` ~127, argparse ~169, `main`.
- `infra/runner/mock_runner.py`, `infra/runner/README.md`.
- `pipez/config.py` — `LEAF_REQUIRE_TESTS`, `LEAF_RETRIES`, `PLAN_*`.
- `pipez/llm.py` — `plan_schema()` (`test_command`, `build_command` optional strings).
- `tests/test_code_leaf.py`, `tests/test_integrate.py`, `tests/test_runner_filter.py`,
  `tests/conftest.py` (forces mock sandbox).
- `.odin/sessions/task-07c-*-summary.md`, `task-07d-*-summary.md`, `task-08-*-summary.md`.

## Deliverables
1. **Runner: authoritative test command.** Add `--test-command` (optional) to
   `pipez_runner.py`. `run_tests`: if a project test command is given, run exactly that
   (from cwd, `PYTHONDONTWRITEBYTECODE=1`), parse `passed`/`failed` from pytest's summary line
   when present (fall back to rc-based 0/1), and record the agent's claimed command separately
   as `claimed`. Only without a project command fall back to the claim, and never execute a
   claim that is not a plausible command (`none`, `n/a`, `-`, empty, "no tests", anything
   without whitespace that is not an executable on PATH or a relative path that exists) —
   return `None` instead. Mirror in `mock_runner.py`. Update `infra/runner/README.md`.
2. **Engine passes it.** In `_execute_code_task`, pass `--test-command <plan.test_command>`
   (shell-quoted) when the plan has one. In the leaf prompt add a `## Environment` section:
   the exact build and test commands; "the repo's virtualenv is at `./.venv` in this worktree
   — use `.venv/bin/python` / `.venv/bin/pip`, never system `python3`"; "the runner re-runs
   `<test_command>` from the worktree root after you finish; your `tests` field is advisory".
   Keep the existing `.pipez/result.json` contract.
3. **Worktree gets the venv.** Right after `git worktree add` (both the branch and the
   `--detach` fixer case), if `<repo>/.venv` exists, `ln -s <repo>/.venv <wt>/.venv`
   (idempotent; skip if present). Do the same for `node_modules` if `<repo>/node_modules`
   exists. Both are already in `.git/info/exclude`.
4. **Failed code leaves fail the run.** In both parent paths, when the root of a plan run
   (`run.plan` present) has any failed/skipped children after retries: do NOT synthesise; raise
   `LLMError("N code tasks failed: <title>; <title>")` so `_run`/`_run_resume` mark the run
   `failed` with that message, and `_run_with_sandbox` keeps the sandbox (its existing
   `ok` check). Resume must then re-run only the non-done leaves on the kept repo and, when
   all are done, proceed to `_integrate_with_fixes` → synthesis → done/deploy. Non-plan
   (pure LLM) runs keep today's partial-synthesis behaviour. Also, in `_synthesize` for plan
   runs, state in the prompt that the synthesiser cannot create or modify files and must
   describe only what the integration section proves.
5. **Tests** (mock sandbox/runner): (a) claimed `"none"` → `tests` is `None`, no shell
   execution; (b) project test command is what runs, claim is recorded as `claimed`;
   (c) worktree has `.venv` symlink after add; (d) a plan run with one failed leaf ends
   `failed` with the "code tasks failed" error, sandbox row `kept`, no `resume_resynthesis`
   log, no artifacts; (e) resume of that run re-executes only the failed leaf and then
   integrates (integration events present) and ends `done`.

## Out of scope
- Changing the retry count, tiers, prompts other than the sections named above, the planner,
  the UI, the deploy stage, the PVE client, or the builder template.
- Resuming run c6973d74ac0e or deploying to the VM (Odin Prime does that after verification).
- Any new dependency.

## Verification criteria
- `grep -n "test-command" infra/runner/pipez_runner.py pipez/engine.py` show the flag defined
  and passed.
- `grep -n "ln -s" pipez/engine.py` shows the venv symlink after worktree add.
- `.venv/bin/python -m pytest -q` from the project root: all tests pass (≥105), including
  the five new cases.
- `python3 -c "import ast,sys; ast.parse(open('infra/runner/pipez_runner.py').read())"` ok.
- No `pkill`, nothing on ports 8400/8401 touched, nothing committed.

## Rules
- Never print secrets. Never run `pkill`. Do not start servers on 8400/8401.
- Do not commit.

## Write summary
`.odin/sessions/task-12-leaf-test-harness-summary.md` — what changed per file, test count,
anything deliberately left. Mandatory last step.
