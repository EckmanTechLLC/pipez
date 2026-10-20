# Task 23 summary: deploy hygiene

Files changed
- pipez/db.py: two migrations (below); `keep`, `expires_at` added to DEPLOYMENT_FIELDS.
- pipez/config.py: DEPLOY_TTL_HOURS (72), DEPLOY_SWEEP_SECONDS (3600), DEPLOY_REPLACE (on).
- pipez/engine.py: `_expiry`, `set_deploy_keep`, `expire_deployments`, `replace_deployments`; `_deploy` sets `expires_at` on ready and calls `replace_deployments` after `deploy_ok` (success path only, covers plan-run and manual deploys).
- pipez/server.py: background `_expire_loop` started in `startup()` after sweep (first pass immediate, exceptions logged, cancelled in a new shutdown handler); `POST /api/deployments/expire`; `PUT /api/runs/{id}/deployment/keep` (404 if no deployment). SSE deployment rows are plain DB rows, so they carry the new columns.
- ui/index.html: `deployBadge` shows expiry/kept and keep/unkeep; new Deployments panel (button, `#deps`, `loadDeps`, refreshed on deployment SSE events and on open; run id click selects the run; keep, Undeploy; destroyed rows behind a "show destroyed" toggle).
- README.md, .env.example: three new env vars documented.
- tests/test_deploy_hygiene.py: 6 tests (new).

Migration lines added
- ALTER TABLE deployments ADD COLUMN keep INTEGER DEFAULT 0
- ALTER TABLE deployments ADD COLUMN expires_at REAL

Decisions
- Un-keeping sets expiry to now + TTL (as the task suggested). Expiry-undeploy failures are reported per row (`expired: false`) and logged as `deploy_expire_failed`.
- Replace test (d) uses DB-seeded ready rows and calls `replace_deployments` directly rather than two full plan runs; the `_deploy` hook itself is exercised by the TTL tests.
- Replaced rows end as `destroyed` via the normal `undeploy`.

Verification
- grep of expires_at/keep/expire_deployments/replace_deployments/set_deploy_keep/routes: met.
- "Deployments" panel present; node --check of extracted inline script: met.
- Full suite: 172 passed (4:56). New file has 6 tests: met.

Deferred / untouched
- No UI browser run was done (syntax check only). Build-sandbox lifecycle, planner, runner, review untouched.
