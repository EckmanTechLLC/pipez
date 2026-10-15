# Task 09 summary: deploy a finished build to a lab VM

## Files
- pipez/deploy.py (new): `Deployer` (provision, upload tarball, extract, install commands, systemd unit, health poll), `systemd_unit`, `app_name`, `make_deploy_sandbox`, `MockDeploySandbox` (temp dir + background process in its own process group, pid file).
- pipez/sandbox.py: `PveSandbox` keyword args `template, vmid_range, name_prefix, cores, memory`; defaults unchanged, cores/memory added to the existing `set_config` call only when set.
- pipez/engine.py: `deploy_factory` ctor arg, `_clean_deploy` (called from `_validate_plan`), `_wants_deploy`, `deploy`, `_deploy`, `undeploy`, `_abort_deploy`, `_tarball`; `deploying` set at end of `_run_resume`; deploy runs in `_run_planned` after the builder sandbox is released; `stop()` handles `deploying`.
- pipez/db.py: `deployments` table + `upsert/get/list/replace_deployment`. pipez/server.py: GET /api/runs/{id}/deployment, GET /api/deployments, POST /deploy, POST /undeploy, SSE `deployment` + snapshot key.
- pipez/llm.py: optional `deploy` in plan schema, mock plan deploy block. pipez/seed_prompts.py: plan_architect `deploy` bullet. pipez/config.py + .env.example: `DEPLOY_AUTO`, `DEPLOY_TEMPLATE`, `DEPLOY_VMID_RANGE`, `DEPLOY_HEALTH_TIMEOUT`.
- ui/index.html: `deployBadge` (Deployed link, failure tail, Deploy/Undeploy), `deploymentByRun` + SSE handling, plan-panel Deploy block, `deploying` status colour.
- tests/test_deploy.py (7 tests), tests/conftest.py (autouse `DEPLOY_AUTO=False`; deploy tests opt in), README "Deploy" + config rows, .odin/project-index.md.

## Verification
1. `.venv/bin/python -m pytest -q tests/`: 100 passed. Met.
2. Mock smoke on 8401 (temp DB/artifacts): run statuses provisioning, running, deploying, done; deployment `ready`, url http://127.0.0.1:54251; GET url -> 200; POST /undeploy -> `destroyed`; curl afterwards -> connection refused (000). 8401 stopped with fuser, temp dir removed, no pipez-sandbox dirs left, no `http.server` processes (pgrep clean). 8400 untouched (200). Met.
3. `node --check` on extracted inline script: ok. Met.
Not exercised: real Proxmox/systemd path (command composition only; no VM provisioned), the UI in a browser.

## Decisions / interpretations
- Auto trigger: `_run_resume` flips the run straight from `done` to `deploying` (no `done` flicker), and `_deploy` runs after the builder sandbox is released (the sandbox release treats `deploying` as success).
- Mock deploy skips install commands starting with `sudo`/`apt`/`apt-get` (logged `skipped: mock`) so tests never touch the host; other commands run locally. Mock uses a free port if the plan's is busy (8080 was busy here) and records the actual URL.
- `{port}` in `start_command` is substituted in both mock and real.
- URL recorded is `http://<ip>:<port>` (no health path).
- `_clean_deploy`: target `vm` without start_command or a valid port is downgraded to `none`; health_path forced to start with `/`; env names validated, values coerced to strings.
- `app_name`: first non-empty `repo_layout` line of the form `name/`, ignoring `src/ tests/ test/`, else `app-<run_id>`.
- Manual deploy requires run `done`, a tarball, `target == vm`, and no active deployment (provisioning/installing/starting/ready). A prior `failed` deployment's kept VM is destroyed before redeploying. Undeploy allowed for `ready` and `failed`.
- Stop while deploying (driver cancelled): VM torn down, deployment `failed` ("stopped by user"), run set back to `done`. Orphaned `deploying` after a server restart is handled by stop only; no auto-recovery on startup.
- Health curl runs inside the sandbox via ssh against `http://<ip>:<port>`; on timeout the error includes `journalctl` tail (pve) or service.log (mock).

## Exact systemd unit generated (app `shop`, env non-empty, port 8080, start `python3 -m http.server {port}`)
[Unit]
Description=shop (deployed by pipez)
After=network-online.target
Wants=network-online.target

[Service]
User=etl
WorkingDirectory=/opt/shop
ExecStart=/bin/bash -lc 'python3 -m http.server 8080'
Restart=on-failure
EnvironmentFile=/opt/shop/.env

[Install]
WantedBy=multi-user.target

(EnvironmentFile line omitted when env is empty. In ExecStart, `\`, `'`, `%` and `$` are escaped as `\\`, `\'`, `%%`, `$$`. `.env` holds `NAME=` lines, mode 600.)

## Left alone
git (no commit), live instance on 8400, infra/, VMs 9000/9001, a foreign `sleep 600` process seen in pgrep (not mine). No live LLM/Proxmox calls.
