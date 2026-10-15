# Task 09: Deploy a finished build to a lab VM

## Objective
After a code run integrates successfully, and when its decisions chose VM
hosting, pipez clones a fresh VM from template 9000 (`ubuntu-base`, not the
builder), installs the app from the run's tarball using commands the plan
specified, runs it as a systemd service, waits for a health check, and records
the URL on the run. The deploy VM is kept (it IS the deliverable) and listed
with the run; it can be torn down from the UI. Everything is mockable offline.

## Reference (read first)
- `.odin/project-index.md`; summaries for 07b/07b-a (`ProxmoxClient`, `PveSandbox.provision` — clone/sshkeys/agent IP/ssh-ready; `sandboxes` table), 07d (`_integrate`, artifacts `repo-<id>.tar.gz`, `runs.plan`, `plan_schema()`), 08 (`stop`, statuses), 04/04b (decisions/answers on the run — the hosting decision key is whatever intake chose, typically `hosting.target` / `deployment.*`; inspect `runs.answers`)
- Template 9000 facts: Ubuntu cloud image, cloud-init user `etl` (passwordless sudo), DHCP on vmbr0, qemu-guest-agent, 2c/4G/20G. Token has `PVETemplateUser` on 9000 and full rights in pool `pipez`. VMIDs for deploys: `PIPEZ_PVE_DEPLOY_VMID_RANGE` default `9200-9299`.
- `pipez/sandbox.py`: reuse `PveSandbox` for the deploy VM (parametrise template id, VMID range, name prefix, and `cores/memory` via `set_config`) rather than writing a second client.

## Design (binding)
- **Plan schema**: optional object `deploy`: `{"target": "vm" | "none", "install_commands": [str], "start_command": str, "port": int, "health_path": str, "env": [{"name": str, "value": str}]}`. `plan_architect` prompt: when the brief's hosting decision is a VM, fill `deploy` for a clean Ubuntu host as user `etl` with passwordless sudo: install the toolchain (apt/pip/npm) non-interactively, install the app from the extracted tarball at `/opt/<app>/`, give a foreground `start_command`, the listening `port` and a `health_path` returning 2xx; secrets go in `env` by NAME only with empty value (the operator fills them). Otherwise `target: "none"`. Mock plan: `target: "vm"`, install `["sudo apt-get install -y python3"]`, start `python3 -m http.server {port}`, port 8080, health `/`.
- **Trigger**: automatically after `integrate_ok` when `plan.deploy.target == "vm"` and `config.DEPLOY_AUTO` (default 1); also manual `POST /api/runs/{id}/deploy` (allowed when the run is `done` with artifacts and no active deployment). `POST /api/runs/{id}/undeploy` destroys the VM.
- **Deployment** (`engine._deploy(run_id)`; run status `deploying` then back to `done`; failures do NOT fail the run — they mark the deployment `failed` and log `deploy_failed` with the tail; the VM is kept for inspection): provision via `PveSandbox` configured with template `PIPEZ_PVE_DEPLOY_TEMPLATE` (9000), range 9200–9299, name `pipez-app-<run_id>`, `cores=2, memory=2048` → `put` the tarball to `/tmp/app.tar.gz` → `sudo mkdir -p /opt/<app> && sudo tar xzf ... -C /opt/<app> && sudo chown -R etl /opt/<app>` → run each `install_commands` entry with `bash -lc` in `/opt/<app>` (log `deploy_install` rows) → write `/etc/systemd/system/<app>.service` (`WorkingDirectory=/opt/<app>`, `ExecStart=/bin/bash -lc '<start_command>'`, `User=etl`, `Restart=on-failure`, `EnvironmentFile=/opt/<app>/.env` if `env` non-empty — write names with empty values) → `systemctl daemon-reload && enable --now` → poll `http://<ip>:<port><health_path>` from the sandbox host side with `curl -sf` through `sandbox.run` (≤ `PIPEZ_DEPLOY_HEALTH_TIMEOUT`, 180 s) → record. `<app>` = plan `repo_layout`'s top-level name if obvious, else `app-<run_id>`.
- **Persistence**: table `deployments(run_id PK, vmid, name, ip, url, status, created_at, ready_at, destroyed_at, error, log TEXT)`; statuses `provisioning|installing|starting|ready|failed|destroyed`. Routes: `GET /api/runs/{id}/deployment`, `GET /api/deployments`. SSE `deployment` events; snapshot includes it.
- **Mock**: with `MockSandbox`, "deploy" runs the install/start steps locally in a temp dir with `start_command` executed in the background (`python3 -m http.server`) and the health check against `127.0.0.1:<port>`; `undeploy` kills it. Pick a free port in tests.
- **UI**: run header shows a **Deployed: http://ip:port** link when ready, status/tail when failed, and **Undeploy**/**Deploy** buttons as appropriate; the plan panel shows the `deploy` block (target, port, health path, install command count).

## Deliverables
1. Plan schema + prompt + mock; `_validate_plan` normalises `deploy`.
2. `PveSandbox` parametrisation (template, VMID range, name prefix, sizing) without changing existing callers' behaviour; `pipez/deploy.py` with the deployment steps as a class used by the engine.
3. `deployments` table + routes + SSE; engine trigger after `integrate_ok`; `deploying` status; manual deploy/undeploy; stop() while deploying tears the deploy VM down (kept=False) and marks `failed`.
4. UI changes; `node --check` passes.
5. `tests/test_deploy.py` (mock): auto-deploy after a successful mock run → deployment `ready`, URL reachable locally (`urllib` GET 200), undeploy → `destroyed` and port closed; failing install (`install_commands: ["false"]`) → deployment `failed` with tail, run still `done`; `target: "none"` → no deployment. README "Deploy" section; `.odin/project-index.md`.

## Out of scope
- Git remotes. TLS/DNS/reverse proxy. Deploying anything but the run's own tarball. Live runs (controller). Committing to git.

## Verification criteria
1. `.venv/bin/python -m pytest -q tests/` passes.
2. Mock smoke on **8401**: full mock run → `done` → `GET /api/runs/{id}/deployment` is `ready` with a `url`; `curl -sf <url>` → 200; `POST /undeploy` → `destroyed`. Stop 8401; clean up (no stray `http.server` processes: check `pgrep -f http.server`).
3. `node --check` passes.

## Write summary
`.odin/sessions/task-09-deploy-to-vm-summary.md`: files, verification output, ambiguities resolved, the exact systemd unit text generated.
