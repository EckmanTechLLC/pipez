# Task 28 — Fault-injection hook to exercise the review fix round on a real run

## Objective
Three live builds passed review with zero `wrong` verdicts, so the fixer → integrate
→ undeploy → redeploy → re-review loop (`_review_with_fixes`) has only ever run
against the mock. Add an explicit, off-by-default test hook:
`PIPEZ_REVIEW_INJECT_WRONG="<substring>"` — in review round 1 only, after the
reviewer's verdicts are parsed, the first verdict whose claim contains the substring
(case-insensitive) is overridden to `wrong` with correction
`"INJECTED BY PIPEZ_REVIEW_INJECT_WRONG (test hook): <original note or claim>"`, the
override is logged (`review_injected`), surfaced in the verdict panel (the verdict
carries `injected: true`), and the normal fix round proceeds. Round ≥ 2 is never
injected, so a correct fixer run ends `passed`.

## Reference (read first)
- `pipez/engine.py` — `_review` (~1460–1515: parse of `.pipez/verdicts.json`, the
  `verifications` row, `set_run_verification`), `_review_with_fixes` (fixer task,
  `_integrate`, undeploy/redeploy, re-review; `config.VERIFY_ROUNDS`), `_review_md`.
- `pipez/config.py` — `VERIFY_*` settings (~200–210) for naming/style.
- `pipez/db.py` `add_verification`/`get_verifications` (verdict JSON shape:
  `{claim, verdict, note, correction, source?}`).
- `ui/index.html` — verdict panel rendering (grep `verdict`, `wrong`, `correction`).
- `tests/test_review.py` — how a review round with `wrong` verdicts drives the fixer
  (`MOCK_WRONG` marker in the mock runner) and how rounds are asserted.
- `README.md`, `.env.example`.

## Deliverables
1. `config.REVIEW_INJECT_WRONG = os.environ.get("PIPEZ_REVIEW_INJECT_WRONG", "")`;
   documented in `.env.example`/`README.md` as a TEST HOOK (never set in normal use).
2. `Engine._inject_wrong(run_id, rnd, verdicts) -> verdicts`: as specified; when the
   substring matches nothing, log `review_inject_miss` and change nothing. Applied in
   `_review` before the verification row is stored and before the pass/fail decision.
3. The stored verdict carries `"injected": true`; `_review_md` for the fixer shows the
   injected correction like any other `wrong` verdict (no special casing); the UI
   verdict panel shows an `injected` badge next to such a verdict.
4. Tests (`tests/test_review_inject.py`): (a) with the hook set to a substring of a
   confirmed claim, round 1 gets one `wrong` verdict with the INJECTED correction,
   `review_injected` is logged, the fixer runs, round 2 is not injected and the run
   ends `passed` with 2 verification rows; (b) a non-matching substring logs
   `review_inject_miss` and the run passes in 1 round; (c) hook empty → no effect.
   Full suite `python -m pytest -q` (use `.venv/bin/python`; ~6 min; timeout ≥
   560 000 ms or nohup + poll; do not assume a hang before 8 minutes; rerun flaky
   failures — `test_stop_kills_remote_runner_group` is known timing-flaky).

## Out of scope
- Any change to the reviewer/fixer prompts, planner (task 27 runs concurrently and
  edits `pipez/seed_prompts.py` plan_architect, `_validate_plan` and
  `tests/test_plan_rules.py` — do not touch those), UI beyond the badge.
- Never run `pkill`/`killall`; never touch ports 8400/8401; never touch Proxmox.

## Verification criteria
- `grep -n "REVIEW_INJECT_WRONG\|_inject_wrong\|review_injected" pipez/config.py pipez/engine.py` non-empty.
- `python -m pytest -q` green; new test file has ≥ 3 tests.

## Write summary
`.odin/sessions/task-28-review-fault-injection-summary.md`: files changed, test
count, anything deferred.
