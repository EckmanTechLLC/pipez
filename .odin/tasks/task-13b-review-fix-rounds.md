# Task 13b — Fix rounds after a failed acceptance review

## Objective
Task 13a stores verdicts and flags the run (`runs.verification` = passed/failed/error) but
stops there. Close the loop: when the review returns any `wrong` verdict, run a fixer leaf on
`integration` with the wrong verdicts (claim + note + correction) as its brief, re-integrate
(build/test/tag/export), replace the deployment with a FRESH deploy VM (destroy the old one,
clone again from the template, install, start, health check — never update in place), and
review again. Repeat up to `PIPEZ_VERIFY_ROUNDS` (default 1). If verdicts are still `wrong`
after the rounds, the run still ends `done` flagged `failed`, app stays deployed, sandbox kept.

## Reference
- `pipez/engine.py`:
  - `_run_planned` (~1085–1125): run → `_deploy(review=True)` → `_review` → `_finish_review`.
  - `_review(run_id, root)` (~1165–1230), `_review_skipped`, `_finish_review`, `reverify`.
  - `_integrate_with_fixes(run_id, root)` (~2064): the existing fixer pattern — creates a
    `module="*"` deep code task with the failure as prompt, `_execute(fix)`, re-`_integrate`.
    Reuse the same mechanics (fixer runs directly on `integration`, merge ff-only).
  - `_integrate` (~1884): re-runnable (tag is `-f`, artifacts re-exported).
  - `_deploy(run_id, review=False)` (~928), `undeploy` / `_destroy_deploy_vm` (~986),
    `ACTIVE_DEPLOY`, `_wants_deploy`.
  - `REVIEW_ROLE`, `_build_tasks`, `_build_done` (reviewer and fixer rows must not break
    the "all build tasks done" logic — fixers are build tasks and must end `done`).
- `pipez/config.py`: `FIX_ROUNDS`, `VERIFY_AUTO`, `VERIFY_TIER`.
- `pipez/db.py`: `verifications` (one row per review; keep history — do NOT clear between
  rounds; add `round` INTEGER column via `MIGRATIONS` and set it), `task_log`.
- `ui/index.html`: verdict panel (`verifySummary`, verifications rendering) — show rounds
  ("review round 2 of 2") and the fixer task row like other code tasks.
- `tests/test_review.py` (mock runner `MOCK_WRONG` marker), `tests/test_deploy.py`
  (`dep` fixture, MockDeploySandbox), `tests/test_control.py` (fix-loop tests).
- `.odin/sessions/task-13a-acceptance-review-summary.md`, `task-08-*-summary.md` (fix loop).

## Deliverables
1. `config.VERIFY_ROUNDS` (`PIPEZ_VERIFY_ROUNDS`, default 1).
2. `Engine._review_with_fixes(run_id, root)` used by `_run_planned` and `reverify`:
   loop — `_review`; if `failed` and rounds < `VERIFY_ROUNDS`: create fixer task
   (role `integrator-engineer`, title "Fix review findings (round N)", tier `deep`,
   kind `code`, module `*`, prompt = the brief's relevant decisions + a numbered list of the
   `wrong` verdicts with note and correction + "make the full build and tests pass; change
   as little as possible; keep public contracts"), `_execute(fix)`, then
   `_integrate(run_id, root)` (on `IntegrationError` fall back to the existing
   `_integrate_with_fixes` behaviour), then redeploy fresh: destroy the current deploy VM
   via the same path `undeploy` uses, then `_deploy(run_id, review=True)`; if the new
   deployment is not `ready`, stop looping (verification stays `failed`, log why). Review
   again. Every step logged on `run:<id>`: `review_fix` {round, task_id, wrong: n},
   `review_redeploy` {round, vmid}, `review_round` {round, verification}.
3. Verifications keep history: a `round` column; `_review` does not clear earlier rows;
   `get_verifications` returns them in order; the run's `verification` flag reflects the
   LAST round. Mock runner: `MOCK_WRONG` still yields one `wrong`; add marker
   `MOCK_WRONG_ONCE` support in the *engine tests* by having the fixer's run drop the
   marker (e.g. the test monkeypatches the reviewer prompt between rounds) — whatever is
   simplest and deterministic; document the approach in the summary.
4. UI: verdict panel groups by round with the verification result per round; the fixer
   task appears in the task tree; run header badge unchanged from 13a.
5. Tests: (a) wrong → fixer runs → re-integrate → old deploy VM destroyed + new one ready
   (MockDeploySandbox: assert a different vmid/name or a destroyed+ready sequence in the
   deployment log) → second review passes → run `done`, `verification=passed`, sandbox
   destroyed, 2 verification rows with rounds 1 and 2; (b) still wrong after
   `VERIFY_ROUNDS=1` → `done`, `verification=failed`, sandbox kept, 2 rows; (c) fixer
   failure → `verification=failed`, run `done`, logged; (d) `VERIFY_ROUNDS=0` → no fixer,
   one row.

## Out of scope
- In-place deployment updates; changes to the reviewer prompt, planner, intake, prefs, PVE
  client, templates; the LLM-run `verify()`; committing; touching the live VM.

## Verification criteria
- `grep -n "VERIFY_ROUNDS\|_review_with_fixes\|review_redeploy" pipez/config.py pipez/engine.py`
  show the config, the loop and the redeploy log.
- `.venv/bin/python -m pytest -q` passes (currently 116; expect ≥120). The suite takes
  ~3 min — Bash timeout ≥ 420000 ms; never conclude it hangs before 6 min.
- No commits, no `pkill`, nothing on ports 8400/8401, no new dependencies.

## Write summary
`.odin/sessions/task-13b-review-fix-rounds-summary.md` — mandatory last step.
