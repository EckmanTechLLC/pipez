# Task 13b summary - review fix rounds

## Changes
- pipez/config.py: `VERIFY_ROUNDS` (`PIPEZ_VERIFY_ROUNDS`, default 1).
- pipez/db.py: migration `verifications.round`; `add_verification(..., round=1)`.
- pipez/engine.py: `_review_with_fixes` (used by `_run_planned` and `_reverify`). It clears old verification rows once at the start, then loops `_review(run, root, rnd)`. While the result is `failed` and rnd <= VERIFY_ROUNDS, it creates the fixer, runs `_execute(fix)`, then `_integrate` (a fixable IntegrationError falls back to `_integrate_with_fixes`). It then calls `undeploy` (destroys the VM) and a fresh `_deploy(review=True)`. If the new deployment is not `ready`, it stops with verification still `failed`. `_review` no longer clears rows and stores the round.
- Fixer: role integrator-engineer, "Fix review findings (round N)", deep, code, module `*`. Its prompt holds the brief, a numbered list of wrong verdicts (claim, problem, correction) and the "full build and tests pass / change as little as possible / keep public contracts" instruction.
- Run-log events: `review_fix` {round, task_id, wrong}, `review_redeploy` {round, vmid}, `review_round` {round, verification}, plus `review_fix_stopped` {round, reason} when the loop gives up.
- ui/index.html: the verdict panel labels each verification "review round N of M" with a per-round passed/failed result. The fixer shows in the task tree like any code task. Run header badge unchanged.
- tests/test_review.py: 4 new tests (pass after fix; exhausted rounds; fixer failure; VERIFY_ROUNDS=0). Two 13a tests adapted: re-verify test sets VERIFY_ROUNDS=0 to keep "replace" semantics; stop test's `_review` wrapper takes `rnd`.

## Decisions
- Wrong-once approach: the test spies `engine._review_md` and strips `MOCK_WRONG` from the reviewer task file from the second review on. The mock runner is unchanged.
- Fixer failure: fixer plan id is `fix<seq>`, so the test uses `PIPEZ_MOCK_RUNNER_FAIL=fix4`. The fixer ends `failed`, so `_build_done` is false and the sandbox is kept. Run ends `done`, verification `failed`.
- Mock deploy sandbox has no vmid, so the new-VM check uses log order (deploy_ok, undeployed, review_redeploy, deploy_ok), not a vmid difference.
- Manual re-verify also runs fix rounds when rounds > 0 and clears history first.

## Verification
- grep shows VERIFY_ROUNDS, _review_with_fixes, review_redeploy: met.
- Full suite: 120 passed (was 116, required >=120): met.
- No commits, pkill, ports 8400/8401, new dependencies or live VM contact: met.
- UI change was not browser-tested (no server started).
