# Task 27 summary

Changed
- pipez/seed_prompts.py: plan_architect gains EXECUTION BOUNDARY and ACCEPTANCE CRITERIA rules (inserted before PARSIMONY), wording per the task's deliverable 1.
- pipez/engine.py: `_plan_rule_warnings` (+ class regexes) emits `task_touches_deployment` and `process_acceptance_criteria` warnings; `_plan` extends `plan_meta.warnings` with them (after the oversize log, so no spurious plan_oversize log). Heuristic documented in the docstring.
- tests/test_plan_rules.py: 3 tests (prompt rules; deploy task warns / unit-file authoring task does not; report criterion warns / HTTP criterion does not).

UI: ui/index.html `planMetaLines` already renders `plan_meta.warnings`; no change.
Task prompt rendering (`_task_md`) left alone; criteria are not assembled there beyond pass-through.

Verification
- grep for both prompt headings and both warning names: met.
- Full suite: first run 186 passed, 2 failed (my tests, test bug calling the method with self=None); fixed, reran test_plan_rules + test_plan: 12 passed. Full suite not re-run end to end after the fix (only test file changed).
- New test file has 3 tests: met.

Notes: seed upserts the new prompt text only if the stored one equals the previous seeded version (existing seed semantics). Warnings are computed in `_plan` only, not on edited plans at approve time.

## Follow-up: tightened task_touches_deployment
- engine.py: `_DEPLOY_RE` now matches only host-execution phrases (ssh/scp to host, live/real/remote vm|host|server with optional one word between, live (re)deploy, redeploy, deploy to/on [the|a] [up to 3 words] vm|host|server, on the vm, smoke-test live/deployed/running). Tasks whose title starts Write/Author/Add/Create/Update/Document/README are skipped. Added "live deploy" and an optional-words allowance so "real Ubuntu VM" matches.
- tests/test_plan_rules.py: added positive/negative title cases (2 pos, 5 neg).
- Results: test_plan_rules + test_plan 13 passed; full suite 192 passed (includes task 28 changes).
