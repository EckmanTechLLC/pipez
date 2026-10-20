# Planner-rule replay — 2026-10-20

3 stored child-run plans (da31df2bfc2a, 53e6dd6c7354, aabf050e8aba) replayed at
deep@medium through the task-27 `plan_architect` prompt (EXECUTION BOUNDARY +
ACCEPTANCE CRITERIA rules), with the parent bundles present locally.

| stage | tier | n | ok% | p50 s | mean $ | mean out-tok | tasks | Δtasks | deploy-match% | forbidden% |
|---|---|---|---|---|---|---|---|---|---|---|
| plan | deep@medium | 3 | 100 | 141.3 | 0.3569 | 14098 | 5.0 | +0.3 | 100 | 0 |

Result: no "live deploy / redeploy to the VM" leaf in any replay (previously 2 of 2
child runs had one); verification gates are local-only. Report-style acceptance
criteria mostly gone; remaining flagged ones are `git diff` / `shellcheck` checks.
The first version of the `task_touches_deployment` warning heuristic was too noisy
(flagged every task mentioning "deploy"); tightened afterwards.
