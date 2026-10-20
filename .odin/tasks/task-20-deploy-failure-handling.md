# Task 20 — Deployment failure inside a plan run: keep the sandbox, flag the run; separate deploy boot timeout

## Objective
Live finding (run c7213225e46a): the deploy VM's guest agent did not appear within the
240 s boot timeout, the deployment ended `failed`, and the run then ended `done` with
verification `skipped` ("deployment is absent") and the build sandbox **released** —
so nothing could be redeployed-and-reviewed. Change: (1) a failed deployment in a plan
run sets `runs.verification = "error"` (reason logged) so `_finish_review` keeps the
build sandbox and the UI shows the run flagged; (2) the deploy sandbox gets its own
boot timeout `PIPEZ_DEPLOY_BOOT_TIMEOUT` (default 600) instead of sharing
`PIPEZ_SANDBOX_BOOT_TIMEOUT`; (3) after a manual `POST /api/runs/{id}/deploy` succeeds
on such a run, `POST /api/runs/{id}/verify` (→ `reverify`) must work against the kept
sandbox and the new deployment.

## Reference (read first)
- `pipez/engine.py` — `_run_planned` (~1300–1366: deploy → review → `_finish_review`),
  `_finish_review` (~1368), `_review_skipped` (~1384), `_deploy` (~1164–1225; failure
  branch sets deployment `failed`), `_review` (~1460–1480: skips when deployment not
  `ready` or no sandbox), `reverify` (~1515), `_release_sandbox` (~1277: kept sandboxes
  keep their in-memory handle), `deploy()` (~1125; manual deploy of a done run).
- `pipez/deploy.py` — `make_deploy_sandbox` (~123), `Deployer.run` (~158).
- `pipez/sandbox.py` — `PveSandbox.__init__` (~201, `boot_timeout` kwarg).
- `pipez/config.py` — `SANDBOX_BOOT_TIMEOUT` (~190), `DEPLOY_TEMPLATE` (~204).
- `pipez/db.py` — `set_run_verification`, `upsert_deployment`, `get_deployment`.
- `ui/index.html` — verification badges (search `verification`), deploy status block.
- `tests/test_review.py`, `tests/test_deploy*.py` (whatever exists) — fixtures; the
  mock deploy sandbox / `MockDeploySandbox` and how a deploy failure is simulated
  (grep `PIPEZ_MOCK_DEPLOY` / `deploy_fail` in tests and `pipez/deploy.py`; add a
  mock failure switch if none exists).
- `README.md`, `.env.example` — env var docs.

## Deliverables
1. `config.DEPLOY_BOOT_TIMEOUT = float(env PIPEZ_DEPLOY_BOOT_TIMEOUT, default 600)`;
   `make_deploy_sandbox` passes `boot_timeout=config.DEPLOY_BOOT_TIMEOUT`. Document in
   `.env.example` and `README.md` next to `PIPEZ_SANDBOX_BOOT_TIMEOUT`.
2. `_run_planned`: when the plan's `deploy.target == "vm"` and, after `_deploy`, the
   deployment row is not `ready`, call a new `_review_error(run_id, reason)` that logs
   `review_error {"reason": "deployment failed: <error>"}` and sets verification
   `"error"` — instead of falling through to `_review` → `skipped`. `VERIFY_AUTO off`
   keeps today's `skipped` behaviour. Result: run `done`, verification `error`,
   sandbox `kept` (via the existing `_finish_review` rule).
3. `reverify`: accept a run whose deployment is `ready` and whose sandbox is
   `kept`/`ready` with a live handle; the review then runs as usual (confirm `_review`
   needs no change; if the kept sandbox object is missing from `_sandboxes`, raise
   `ValueError("the build sandbox is kept but not attached (server restarted)")`).
4. UI: the verification badge for `error` shows the logged reason as its title
   (tooltip) if not already; the deployment block keeps showing the failure text and
   the existing deploy button so a manual redeploy is one click.
5. Tests (`tests/test_deploy_failure.py`): (a) a plan run with a VM deploy whose mock
   deployment fails ends `done`, verification `error`, sandbox `kept`, and an event
   `review_error` is logged; (b) with `VERIFY_AUTO` off the same run ends `skipped`;
   (c) after a successful manual `deploy()` on that run, `reverify()` runs the review
   and the run gets a verification (`passed` with the mock reviewer); (d)
   `make_deploy_sandbox` uses `DEPLOY_BOOT_TIMEOUT` (monkeypatch config, assert
   `boot_timeout`). Full suite `python -m pytest -q` passes (~4–5 min; timeout
   ≥ 560 000 ms or nohup + poll; do not assume a hang before 8 minutes; rerun flaky
   failures).

## Out of scope
- Automatic deploy retry; re-attaching kept sandboxes after a server restart;
  template changes (task 21); any runner/planner change.
- Never run `pkill`/`killall`; never touch ports 8400/8401; never touch Proxmox.

## Verification criteria
- `grep -n DEPLOY_BOOT_TIMEOUT pipez/config.py pipez/deploy.py` non-empty.
- `grep -n "_review_error" pipez/engine.py` shows definition + call in `_run_planned`.
- `python -m pytest -q` green; new test file has ≥ 4 tests.

## Write summary
`.odin/sessions/task-20-deploy-failure-handling-summary.md`: files changed, test
count, anything deferred.
