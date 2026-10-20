# Task 27 — Planner prompt: no deployment/remote steps as tasks; product-observable acceptance criteria

## Objective
Two recurring planner habits seen on live child runs: (a) it schedules a leaf like
"Live deploy to the real Ubuntu VM" / "verification gate and live redeploy" although
build tasks run in an isolated sandbox and pipez performs the deployment itself after
integration (rejected by the human 2 of 2 times); (b) acceptance criteria are written
about the builder's *report* ("the report gives the npm ci rc and counts", "the
report confirms by git diff --stat …"), which the acceptance reviewer can only mark
`unverifiable` (5 of 22 verdicts on run aabf050e8aba, 5 of 38 on 53e6dd6c7354).
Fix both in the planner prompt (and the task-prompt rendering if criteria are
assembled there), and add a validation warning so a plan that still does it is
flagged in `plan_meta.warnings`.

## Reference (read first)
- `pipez/seed_prompts.py` — `plan_architect` (the planner system prompt), the
  acceptance-criteria wording in it, `acceptance_reviewer`; `seed()` semantics
  (upsert only when the stored text equals the previous seeded version).
- `pipez/engine.py` — `_plan_prompt` (~555), `_validate_plan` (~600–660: where
  `warnings` are produced), `_task_md` (how a leaf's acceptance criteria reach the
  builder), `_review_md` (how criteria reach the reviewer).
- `pipez/llm.py` `plan_schema()` — task fields (`acceptance`, `title`, `prompt`).
- `tests/test_plan.py`, `tests/test_review.py` — fixtures for plan validation and
  review markdown.
- `.odin/sessions/bench-plan-2026-10-20/bench-*.json` — stored plan outputs (for
  phrasing examples of the bad patterns; raw plans under `output`).

## Deliverables
1. `plan_architect` gains two rules (exact wording is yours, keep it short):
   - EXECUTION BOUNDARY: tasks run inside an isolated build sandbox with no access to
     any VM, host or remote system. Never create a task that deploys, provisions,
     ssh-es, redeploys, smoke-tests a live host, or "reports the /healthz result of
     the VM". Deployment is performed by pipez after integration from `deploy`
     (install_commands/start_command/port/health_path) and reviewed over HTTP by a
     separate reviewer. A final "verification gate" task may only run the project's
     own build/test commands locally.
   - ACCEPTANCE CRITERIA are product-observable statements a reviewer can check on
     the deployed app or in the repository: HTTP status/body for a request, CLI
     output, file present with content, test suite passes. Never criteria about the
     builder's report, process, diffs, what was "marked NOT RUN", or which tools were
     unavailable.
2. `_validate_plan` warnings (non-fatal, logged in `plan_meta.warnings` as today):
   - `task_touches_deployment`: a task whose title/prompt matches
     `\b(deploy|redeploy|provision|ssh|scp|systemctl .* on the vm|live vm|real vm)\b`
     (case-insensitive) AND is not clearly file-authoring (allow when the prompt also
     says "write"/"author"/"script file"/"unit file" within the same task and does not
     say "run"/"execute"/"ssh"). Keep the heuristic simple and documented in a
     comment; false positives are only warnings.
   - `process_acceptance_criteria`: any acceptance criterion matching
     `\b(the report|build report|git diff|marked (as )?NOT RUN|tool unavailable|
     shellcheck)\b` (case-insensitive); the warning lists the task id and criterion.
3. The plan gate view (`_plan_view` in server.py / the UI's plan panel) already shows
   `warnings` — verify; if the UI does not render `plan_meta.warnings`, add a one-line
   warning list above the task list in the plan panel.
4. Tests (`tests/test_plan_rules.py`): (a) seeded prompt contains both rules; (b)
   `_validate_plan` warns on a "Live deploy to the real Ubuntu VM" task and not on
   "Write the systemd unit deploy/pipez.service"; (c) warns on a criterion "The report
   gives the npm ci rc" and not on "POST /shorten with javascript: returns 400 with
   {\"error\":\"invalid_url\"}". Full suite `python -m pytest -q` (use
   `.venv/bin/python`; ~6 min; timeout ≥ 560 000 ms or nohup + poll; do not assume a
   hang before 8 minutes; rerun flaky failures — `test_stop_kills_remote_runner_group`
   is known timing-flaky).

## Out of scope
- Rejecting plans automatically; reviewer prompt changes; runner; bench runs
  (Odin Prime replays 2 stored plans afterwards).
- Never run `pkill`/`killall`; never touch ports 8400/8401; never touch Proxmox.

## Verification criteria
- `grep -n "EXECUTION BOUNDARY\|ACCEPTANCE CRITERIA" pipez/seed_prompts.py` non-empty;
  `grep -n "task_touches_deployment\|process_acceptance_criteria" pipez/engine.py`.
- `python -m pytest -q` green; new test file has ≥ 3 tests.

## Write summary
`.odin/sessions/task-27-planner-prompt-rules-summary.md`: files changed, the exact
rule text added, test count, anything deferred.
