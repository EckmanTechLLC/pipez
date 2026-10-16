# Task 13a — Acceptance review stage for code runs (reviewer leaf, no fix loop yet)

## Objective
Today "tests pass" (written by the same agent) is the only quality gate on a code run.
Add an independent acceptance review: after the deploy VM is `ready` and while the build
sandbox is still alive, a read-only reviewer leaf (tier `standard` — Sonnet, a different
model than the Opus builders) runs the full test suite, exercises the deployed app over
HTTP against every acceptance criterion in the plan, checks the code against the brief's
decisions, and writes verdicts. Verdicts land in the existing `verifications` table/panel;
the run gets a `verification` flag (`passed` / `failed` / `skipped` / `error`). The run
always ends `done` (flagged, app stays deployed; sandbox kept when verification failed).
The fix-and-redeploy loop is task 13b — NOT here.

## Reference
- `pipez/engine.py`:
  - `_run_with_sandbox` / `_run_planned` (~1085–1120): current order is run → release
    sandbox → `_deploy`. The new order for plan runs must be run → `_deploy` → review →
    release sandbox (keep if review failed and `SANDBOX_KEEP_ON_FAILURE`).
  - `_deploy` (~928), `_wants_deploy`, `ACTIVE_DEPLOY`, `db.get_deployment(run_id)` (has
    `url`, `status`), deploy spec `health_path`.
  - `_execute_code_task` (~1483+): worktree creation (`direct` = detached at `integration`
    for module `*`), runner cmd (~1551), result parsing, `_task_md` prompt builder (~1430).
  - `verify()` (~1187): the existing LLM-run verifier — keep it for non-plan runs; plan runs
    route `/api/runs/{id}/verify` to the new review (400 if the sandbox is gone).
  - `_integrate` return `{"steps","tag","files_changed"}`; `integrate_ok` log entry.
- `infra/runner/pipez_runner.py`: `read_result`, `git_state`, `run_tests`, `main`, `out_obj`.
  `infra/runner/mock_runner.py`, `infra/runner/README.md`.
- `pipez/llm.py`: `VERDICTS_SCHEMA` (claim/verdict∈{confirmed,unverifiable,wrong}/note/correction).
- `pipez/db.py`: `MIGRATIONS` (append-only), `verifications` table, `add_verification`,
  `get_verifications`, `set_run_status`, `finish_run`.
- `pipez/config.py`: `DEPLOY_*`, `FIX_ROUNDS`, `SANDBOX_KEEP_ON_FAILURE`, `MODEL_TIERS`.
- `pipez/server.py`: `/api/runs/{id}/verify`, `/verifications`, SSE snapshot (~353).
- `pipez/seed_prompts.py`: add `acceptance_reviewer` (prompt rows are seeded/updated by
  `seed_prompt`, so new defaults reach existing DBs).
- `ui/index.html`: `verifySummary(run)` (~456), `deployBadge`, run header (~397), SSE handlers
  (~300–322).
- Tests: `tests/test_deploy.py` (MockDeploySandbox, `dep` fixture), `tests/test_code_leaf.py`,
  `tests/test_integrate.py`, `tests/conftest.py`.

## Deliverables
1. **Runner review mode.** `--review` flag: the agent is expected to write
   `.pipez/verdicts.json` = `{"summary": str, "ok": bool, "verdicts": [VERDICTS_SCHEMA items]}`
   instead of `result.json`; the runner must NOT require a commit, must fail (`ok:false`,
   error "reviewer modified the worktree") if `git status --porcelain` shows tracked
   changes or new files outside `.pipez/`, and must not run `run_tests` (the reviewer
   runs tests itself and reports). Output JSON adds `verdicts` (parsed file or null).
   Mirror in `mock_runner.py` (emit two confirmed verdicts, or one `wrong` if the task
   prompt contains the marker `MOCK_WRONG`). Document in README.
2. **Reviewer leaf.** `Engine._review(run_id, root)`: creates a task row
   (role `acceptance-reviewer`, title "Acceptance review", kind `code`, module `*`,
   tier `config.VERIFY_TIER`), executes it via `_execute_code_task` on a detached worktree
   at `integration`, with the runner in `--review` mode, and a prompt built from: the brief;
   the plan's architecture; EVERY plan task's title + acceptance list (numbered, these are
   the claims to verdict); the deploy URL and `health_path`; the test command; rules:
   read-only, run the full test suite, exercise each criterion against the URL with curl or
   `.venv/bin/python` + httpx/urllib, check brief decisions (license, storage, auth,
   stack), verdict every criterion, `wrong` requires a concrete `correction`. Seed prompt
   `acceptance_reviewer` holds the rules text. On completion store
   `db.add_verification(run_id, root_id, verdicts, summary)`; set `runs.verification` to
   `passed` (no `wrong`), `failed` (any `wrong`), `error` (runner failed / no verdicts).
   If the deployment is not `ready` or there is no plan: `skipped` (log why).
3. **Ordering + states.** In the planned-run path: after `_run_resume` and `_deploy`
   (deployment `ready`), set run status `verifying`, run `_review`, then `finish`: status
   `done`; release the sandbox: destroy only if all tasks done AND verification ∉
   {failed, error}, else keep per `SANDBOX_KEEP_ON_FAILURE`. Config:
   `VERIFY_AUTO` (`PIPEZ_VERIFY_AUTO`, default 1), `VERIFY_TIER` (`PIPEZ_VERIFY_TIER`,
   default `standard`). `stop()` must treat `verifying` like `running` for plan runs
   (cancel the driver, mark cancelled, keep sandbox) — today it raises "not stoppable".
   Migration: `ALTER TABLE runs ADD COLUMN verification TEXT`.
4. **API + UI.** `/api/runs/{id}/verify` on a plan run re-runs `_review` when the sandbox
   row is `kept`/`ready` and the deployment is `ready` (else 400 with the reason); run JSON
   includes `verification`. UI run header: badge `verified ✓` (green) / `verification
   failed` (red, click scrolls to the verdict panel) / `review error`; the existing verdict
   panel renders the stored verdicts; the manual button reads "re-verify" for plan runs.
5. **Tests** (mock sandbox + MockDeploySandbox + mock runner): (a) auto path — run ends
   `done`, `verification=passed`, sandbox destroyed, deployment ready, a verification row
   with 2 verdicts; (b) `MOCK_WRONG` → `verification=failed`, run `done`, sandbox `kept`;
   (c) review skipped when `DEPLOY_AUTO` is off; (d) runner `--review` refuses a dirty
   tree; (e) stop during `verifying` cancels cleanly.

## Out of scope
- Fix rounds / redeploy after a failed review (task 13b). Any change to the planner,
  intake, prefs, PVE client, templates, deploy recipe, or the LLM-run `verify()` logic.
- Running against the live VM; deploying; committing.

## Verification criteria
- `grep -n "\-\-review" infra/runner/pipez_runner.py pipez/engine.py` show the mode defined
  and passed; `grep -n "verification" pipez/db.py pipez/server.py ui/index.html` show the
  column, API field and badge.
- `.venv/bin/python -m pytest -q` passes (currently 107; expect ≥112). The suite takes
  ~2.5 min — use a Bash timeout ≥ 400000 ms; never conclude it hangs before 5 min.
- No commits, no `pkill`, nothing on ports 8400/8401, no new dependencies.

## Write summary
`.odin/sessions/task-13a-acceptance-review-summary.md` — per-file changes, test count,
anything deliberately left for 13b. Mandatory last step.
