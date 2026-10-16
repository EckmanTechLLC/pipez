# Task 13a summary - acceptance review stage

## Changes
- infra/runner/pipez_runner.py: `--review` flag, `read_verdicts`, `review_dirty`, `review_main` (no commit required, no run_tests, fails with "reviewer modified the worktree: <paths>" on tracked changes or new non-ignored files outside .pipez/, stdout gains `verdicts` = parsed file or null); review-specific system prompt append.
- infra/runner/mock_runner.py: `--review` (2 confirmed verdicts, or 1 `wrong` when task file has MOCK_WRONG; same dirty check); SLEEP env now applies before review too. README.md documents review mode.
- pipez/config.py: `VERIFY_AUTO`, `VERIFY_TIER`. pipez/db.py: migration `runs.verification`, `set_run_verification`. pipez/seed_prompts.py: `acceptance_reviewer`.
- pipez/engine.py: `REVIEW_ROLE`; `_run_planned` order run -> deploy -> review -> release; `_deploy(review=)` hands over as `verifying` (no `done` flicker); `_review`, `_review_md`, `_review_skipped`, `_finish_review`, `reverify`/`_reverify`, `_build_tasks`/`_build_done`; `_execute_code_task` review branch (`--review`, no test gating, no merge, returns verdicts); `stop()` allows `verifying` for plan runs; `resume`/`_resume_task` ignore reviewer rows.
- pipez/server.py: `/api/runs/{id}/verify` on plan runs calls `engine.reverify` (400 with reason). Run JSON carries `verification` (SELECT *).
- ui/index.html: `reviewBadge` (verified / verification failed / review error, re-verify button), `showVerdicts` (badge click selects root task, shows verdict panel), Stop button during `verifying` for plan runs.
- tests/test_review.py (9 tests); .odin/project-index.md section added.

## Decisions
- Reviewer task is a child of the root (kind code, module *), but excluded from resume, "all tasks done" checks. Verdict row is stored under the root task id so the existing panel shows it.
- `verification=skipped` also when `PIPEZ_VERIFY_AUTO=0` (logged `review_skipped`). Verification is reset to null at the start of each planned run/resume.
- A stop during deploy now keeps the sandbox (builder is alive during deploy); stop during review: run cancelled, review task cancelled, sandbox kept, deployment untouched.
- Re-verify clears old verification rows (replaces, not appends); sandbox destroyed after a pass, so re-verify is then 400.

## Verification
- grep for `--review` in runner/engine and `verification` in db/server/ui: met.
- Full suite: 116 passed (was 107, required >=112): met.
- No commits, pkill, port 8400/8401 use, new dependencies, or live VM contact: met.

## Left for 13b
- Fix rounds / redeploy after a failed review. Real-SDK review mode was only unit-tested with a faked session (no live run).
