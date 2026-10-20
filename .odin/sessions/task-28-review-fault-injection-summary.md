# Task 28 summary

Changed
- pipez/config.py: `REVIEW_INJECT_WRONG` (env `PIPEZ_REVIEW_INJECT_WRONG`, default empty).
- pipez/engine.py: `Engine._inject_wrong(run_id, rnd, verdicts)`; called in `_review` after verdict parsing, before the pass/fail decision and the verification row. Round 1 only; logs `review_injected` or `review_inject_miss`; sets `injected: true`.
- ui/index.html: `injected` badge next to the verdict in the verdict panel.
- .env.example, README.md: documented as a test hook (appended; no other lines touched).
- tests/test_review_inject.py: 3 tests (inject -> fix round -> passed with 2 rows; miss; empty hook).

Verification
- grep for REVIEW_INJECT_WRONG/_inject_wrong/review_injected in config.py/engine.py: met.
- New test file has 3 tests, all pass: met.
- Full suite: 189 passed, 2 failed (tests/test_plan_rules.py::test_deployment_task_warning and ::test_process_criteria_warning, AttributeError). Those belong to the concurrent task 27 work (not touched by me), so the suite is not fully green at this moment.

Notes
- `_review_md` unchanged (injected correction appears like any wrong verdict); the fixer prompt test confirms it.
- Not committed.
