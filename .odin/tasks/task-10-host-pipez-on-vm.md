# Task 10: Host pipez on its own VM (`pipez`, cloned from 9000)

## Objective
Stand up a VM on `etl-pve` that runs pipez as a systemd service, reachable on
the lab network, with an idempotent deploy script that rsyncs the working tree
from this box, installs/updates the venv, copies `.env` securely, and restarts
the service. Dev continues on .13; this VM becomes the instance you actually
use. Nothing under `pipez/` changes.

## Reference (read first)
- `infra/pve/README.md`, `infra/pve/make-builder-template.sh` and `smoke-sandbox.sh` (SSH-to-root conventions, `qm` commands, cloud-init `sshkeys`, waiting for the guest agent, Ubuntu **26.04** base needs Python 3.12 from the deadsnakes PPA — see `pipez-builder-vendor.yaml`), `.odin/sessions/task-07a-builder-template-summary.md`
- `/home/etl/projects/pipez/.env` (never print values; copy the file as-is, mode 600), `README.md` (run instructions, env vars), `requirements.txt`
- Token scope: the pipez VM must live in pool `pipez` (so the token can manage it) — but this VM is created as root via `qm`, so pool membership is `qm set <id> --pool pipez`... (`qm` has no `--pool`; use `pvesh set /pools/pipez -vms <id>`). Pick VMID **9090** (outside sandbox 9100–9199 and deploy 9200–9299 ranges).

## Design (binding)
- `infra/pve/make-pipez-vm.sh` (root over SSH, idempotent; refuses if 9090 exists unless `--force`): `qm clone 9000 9090 --name pipez --full 1`; `qm set 9090 --cores 4 --memory 8192 --cpu host --ciuser etl --ipconfig0 ip=dhcp --sshkeys <root@etl-pve key + ~/.ssh/pipez_ed25519.pub + ~/.ssh/id_ed25519.pub from this box>`; `qm disk resize 9090 scsi0 40G`; start; wait for agent + cloud-init; `pvesh set /pools/pipez -vms 9090`; in-guest (via `qm guest exec` or SSH as etl): deadsnakes PPA, `python3.12 python3.12-venv git rsync`, `sudo mkdir -p /opt/pipez && chown etl`. Prints the VM's IP at the end and writes it to `infra/pve/pipez-vm.env` (`PIPEZ_VM_IP=...`, `PIPEZ_VM_ID=9090`).
- `infra/deploy-pipez.sh` (run from this box as etl, idempotent): reads `infra/pve/pipez-vm.env`; `rsync -az --delete` the project to `etl@<ip>:/opt/pipez/app/` excluding `.venv .git .odin/index-cache.json pipez.sqlite3* pipez.log artifacts/ __pycache__ .pytest_cache infra/pve/pipez-vm.env`; `scp` `.env` to `/opt/pipez/app/.env` (mode 600) then append/override `PIPEZ_HOST=0.0.0.0`, `PIPEZ_PORT=8400`, `PIPEZ_DB=/opt/pipez/data/pipez.sqlite3`, `PIPEZ_ARTIFACTS_DIR=/opt/pipez/data/artifacts` (data dir created, owned by etl); on the VM: `python3.12 -m venv /opt/pipez/venv` if missing, `pip install -r requirements.txt`; install `/etc/systemd/system/pipez.service` (`User=etl`, `WorkingDirectory=/opt/pipez/app`, `ExecStart=/opt/pipez/venv/bin/python run.py`, `Restart=always`, `EnvironmentFile=` not needed — config.py reads `.env`); `systemctl daemon-reload && enable --now pipez && systemctl restart pipez`; wait for `curl -sf http://<ip>:8400/api/runs`; print the URL. The pipez VM needs `~/.ssh/pipez_ed25519` to reach sandboxes: copy the private key to `/home/etl/.ssh/pipez_ed25519` (mode 600) on the VM as part of deploy.
- `infra/pve/README.md`: new section for the pipez VM (create, deploy, logs `journalctl -u pipez -f`, where data lives, how to roll back = re-run deploy from a different checkout).

## Deliverables
1. `infra/pve/make-pipez-vm.sh`, executed; VM 9090 running, in pool `pipez`, IP recorded in `infra/pve/pipez-vm.env` (git-tracked is fine — it is just an IP/ID).
2. `infra/deploy-pipez.sh`, executed; pipez answering on `http://<ip>:8400/` with the UI; `systemctl status pipez` active; a mock run is NOT needed — just `GET /api/runs` → 200 and `GET /` containing `pipez`.
3. From the VM, prove the sandbox path works end-to-end without root: run the equivalent of 07b-a's live check **on the VM** (`/opt/pipez/venv/bin/python` with `PYTHONPATH=/opt/pipez/app`): provision a sandbox, `run("claude --version")`, teardown; pool empty afterwards.
4. README sections; `.gitignore` additions if any new local files are produced.

## Out of scope
- Any change under `pipez/`, `ui/`, `tests/` (another agent is editing them). TLS, DNS, reverse proxy, backups. Touching VMs other than 9090 and the one sandbox you create. Stopping the dev instance on .13:8400. Committing to git.

## Verification criteria
1. `ssh root@192.168.50.19 qm config 9090` shows name `pipez`, 4 cores, 8192 MB, `cpu: host`; `pvesh get /pools/pipez` lists 9090.
2. `curl -sf http://<ip>:8400/api/runs` → 200 from this box; `curl -s http://<ip>:8400/ | grep -ci pipez` ≥ 1.
3. Deliverable 3's live check output (clone→SSH-ready seconds) and empty pool afterwards (9090 itself is in the pool — "empty" means no 91xx members).
4. `bash -n` on both scripts; re-running `deploy-pipez.sh` a second time succeeds (idempotent).

## Write summary
`.odin/sessions/task-10-host-pipez-on-vm-summary.md`: files, VM IP, trimmed outputs, anything about the host that surprised you.
