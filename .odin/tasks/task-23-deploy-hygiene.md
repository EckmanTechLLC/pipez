# Task 23 — Deploy hygiene: TTL auto-undeploy, keep flag, deployments page, redeploy replaces

## Objective
App VMs (92xx) currently live until someone clicks Undeploy. Add: (1) a TTL after
which a ready deployment is undeployed automatically unless marked `keep`; (2) a keep
toggle (API + UI); (3) a Deployments panel in the UI listing all deployments with
URL, age, expiry, keep/undeploy; (4) "redeploy = replace": when a child run's VM
deployment becomes ready, other ready deployments of the same project (not `keep`)
are undeployed so a project has one live VM.

## Reference (read first)
- `pipez/db.py` — `deployments` table (~132), `DEPLOYMENT_FIELDS`, `upsert_deployment`
  (~289), `list_deployments`, migrations list (ALTER TABLE … pattern), `runs.project_id`
  / `parent_run_id`, `get_run`.
- `pipez/engine.py` — `_deploy` (~1164; where status becomes `ready` and `deploy_ok` is
  logged), `undeploy` (~1263), `_publish_deployment`, `sweep()` (pattern for a
  maintenance pass), `_run_planned` (deploy → review → `_finish_review`), `deploy()`.
- `pipez/server.py` — `startup()` (~36–48: `recover()`, `sweep()`), routes
  `/api/deployments`, `/api/runs/{id}/deploy|undeploy`, `/api/sandboxes/sweep`, the
  `PinBody`-style Pydantic bodies, SSE snapshot (`deployment` map, ~grep
  `"deployment":`).
- `pipez/config.py` — `SWEEP_DESTROY` (~197) for the env-var style.
- `ui/index.html` — `deployBadge` (~578), `deploymentByRun` + SSE handling (~266, ~311,
  ~324), preferences panel (~1221–1275) as the pattern for a new panel/tab, nav
  markup for panels (grep the preferences panel's toggle/button).
- `tests/test_deploy_failure.py`, `tests/test_resilience.py` — fixtures driving
  deploys with the mock deploy sandbox; `tests/test_server*.py` for route tests.
- `README.md`, `.env.example`.

## Deliverables
1. Schema + config: migrations `ALTER TABLE deployments ADD COLUMN keep INTEGER
   DEFAULT 0` and `ADD COLUMN expires_at REAL`; add both to `DEPLOYMENT_FIELDS`.
   `config.DEPLOY_TTL_HOURS = float(env PIPEZ_DEPLOY_TTL_HOURS, default 72)` (0 = no
   expiry), `config.DEPLOY_SWEEP_SECONDS = float(env PIPEZ_DEPLOY_SWEEP_SECONDS,
   default 3600)`, `config.DEPLOY_REPLACE = env PIPEZ_DEPLOY_REPLACE != "0"`. Document
   all three in `.env.example` and `README.md`.
2. Engine: when a deployment becomes `ready`, set `expires_at = now + TTL*3600` (None
   when TTL is 0 or `keep`). `set_deploy_keep(run_id, keep: bool)` → updates `keep`,
   recomputes `expires_at` (None when keep; now+TTL from the row's `created_at`…
   simpler: from now, when un-keeping), publishes, logs `deploy_keep`.
   `expire_deployments()` → for each `ready` row with `expires_at` not None and `<
   now` and `keep == 0`: `undeploy()`, log `deploy_expired`; returns a report list;
   errors per row are logged and do not stop the pass. `replace_deployments(run_id)`
   → if `config.DEPLOY_REPLACE` and the run's deployment is `ready`: undeploy every
   other `ready`, non-keep deployment whose run has the same `project_id`; log
   `deploy_replaced {"replaced": [...]}`. Call it in `_deploy` right after the
   `deploy_ok` log (both plan-run and manual deploy paths), never on failure.
3. Server: in `startup()`, after `sweep()`, start a background loop task that calls
   `engine.expire_deployments()` every `DEPLOY_SWEEP_SECONDS` (first pass immediately,
   exceptions logged, loop never dies); cancel it on shutdown. Routes: `POST
   /api/deployments/expire` (runs a pass, returns the report), `PUT
   /api/runs/{run_id}/deployment/keep` body `{"keep": bool}` (404 no such deployment).
   The SSE `deployment` rows include the new columns automatically (verify).
4. UI: (a) `deployBadge` shows `expires in Nh` (or `kept`) next to the URL and a
   `keep`/`unkeep` toggle button; (b) a "Deployments" panel (same toggle mechanism as
   the preferences panel) listing `/api/deployments` rows: run id (click selects the
   run), URL link, status, age, expires-in, keep toggle, Undeploy button; refreshed on
   `deployment` SSE events and on open. Rows with status `destroyed` are shown
   collapsed under a "show destroyed" toggle.
5. Tests (`tests/test_deploy_hygiene.py`): (a) a ready deployment gets `expires_at`
   ≈ now + TTL; TTL 0 → None; (b) `expire_deployments()` undeploys an expired
   non-keep row, leaves a keep row and a future row alone; (c) `set_deploy_keep`
   clears/sets `expires_at`; (d) a child run (same project) reaching `ready`
   undeploys the parent's ready deployment but not a `keep` one; with
   `DEPLOY_REPLACE` off nothing is replaced; (e) routes: `PUT …/deployment/keep` and
   `POST /api/deployments/expire` work with the test client. Full suite
   `python -m pytest -q` passes (use `.venv/bin/python`; ~5 min; timeout ≥ 560 000
   ms or nohup + poll; do not assume a hang before 8 minutes; rerun flaky failures).

## Out of scope
- Build-sandbox (91xx) lifecycle; HTTPS/reverse proxy; cost of VMs; any planner,
  runner or review change.
- Never run `pkill`/`killall`; never touch ports 8400/8401; never touch Proxmox.

## Verification criteria
- `grep -n "expires_at\|keep" pipez/db.py pipez/engine.py pipez/server.py` show the
  columns, `expire_deployments`, `replace_deployments`, `set_deploy_keep`, routes.
- `grep -n "Deployments" ui/index.html` shows the panel; `node --check` on the
  extracted inline script passes (extract `<script>` bodies to a temp file).
- `python -m pytest -q` green; new test file has ≥ 5 tests.

## Write summary
`.odin/sessions/task-23-deploy-hygiene-summary.md`: files changed, test count,
migration lines added, anything deferred.
