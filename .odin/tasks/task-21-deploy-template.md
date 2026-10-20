# Task 21 — Deploy base template 9002 with qemu-guest-agent preinstalled (script only)

## Objective
Every deploy VM is a clone of template 9000 whose cloud-init vendor snippet
(`base-vendor.yaml`: `package_update: true` + apt-installing `qemu-guest-agent`, git,
curl, zellij, ttyd downloads) re-runs on each clone, so the guest agent — which pipez
waits for to learn the IP — only appears after apt finishes (today > 240 s). Provide a
script that bakes a dedicated deploy template **9002 `pipez-app-base`** from 9000 with
the agent, python3, python3-venv, curl, ca-certificates, rsync already installed, and a
minimal per-clone vendor snippet that installs nothing. Odin Prime runs the script;
this task only writes it. Do NOT run it, do NOT touch Proxmox.

## Reference (read first)
- `infra/pve/make-builder-template.sh` — the exact pattern (HOST, `rmt`, snippet scp,
  clone from 9000 with `--full 1`, ssh keys, start, wait for agent, provisioning
  commands over ssh, verify, stop, `qm template`, pool). Mirror it.
- `infra/pve/pipez-builder-vendor.yaml` and `pipez-sandbox-vendor.yaml` — the two-layer
  pattern: a bake-time vendor snippet vs a per-clone snippet.
- `infra/pve/README.md` — add a section.
- `pipez/config.py` `DEPLOY_TEMPLATE` (~204), `pipez/deploy.py` `make_deploy_sandbox`
  (~123: `name_prefix="pipez-app-"`, cores 2, mem 2048), `pipez/sandbox.py` clone code
  (which `cicustom` snippet a clone gets, if any — grep `cicustom`/`snippet`).
- `.env.example`, `README.md` — `PIPEZ_PVE_DEPLOY_TEMPLATE`.

## Deliverables
1. `infra/pve/pipez-app-vendor.yaml` (bake-time): `package_update: true`, packages
   `qemu-guest-agent python3 python3-venv python3-pip curl ca-certificates rsync
   gnupg`, `runcmd: systemctl enable --now qemu-guest-agent`. No zellij/ttyd.
2. `infra/pve/pipez-app-clone-vendor.yaml` (per-clone, minimal): no packages, no
   `package_update`; `runcmd: [systemctl enable --now qemu-guest-agent]` only.
3. `infra/pve/make-app-template.sh [--force]`: clone 9000 → 9002 (`--full 1`), name
   `pipez-app-base`, cores 2 / memory 2048, `--ciuser etl --ipconfig0 ip=dhcp
   --sshkeys` (same key handling as the builder script), `--cicustom
   vendor=local:snippets/pipez-app-vendor.yaml`, start, wait for the agent/IP, wait
   for cloud-init (`cloud-init status --wait`), verify `qemu-guest-agent` active and
   `python3 -m venv /tmp/v` works, then `qm set 9002 --cicustom
   vendor=local:snippets/pipez-app-clone-vendor.yaml`, `cloud-init clean --logs`,
   stop, `qm template 9002`. `--force` destroys an existing 9002 first. Refuse to run
   if 9002 exists without `--force`. Never touch 9000/9001/9090 or any 91xx/92xx VM.
4. Docs: `infra/pve/README.md` section "pipez-app-base (9002)"; `.env.example` and
   `README.md`: `PIPEZ_PVE_DEPLOY_TEMPLATE=9002` with one line of why (do not change
   the code default in `config.py`).
5. `bash -n` on the script; `python3 -c "import yaml; yaml.safe_load(open(...))"` on
   both snippets (if PyYAML is in `.venv`, else skip and note it).

## Out of scope
- Running the script, any Proxmox/VM action, engine changes (task 20), tests.
- Never run `pkill`/`killall`; never touch ports 8400/8401.

## Verification criteria
- Files from deliverables 1–3 exist; `bash -n infra/pve/make-app-template.sh` exits 0.
- The script contains the `--force` guard and the two `qm set ... --cicustom` steps.
- README/.env.example mention `PIPEZ_PVE_DEPLOY_TEMPLATE=9002`.

## Write summary
`.odin/sessions/task-21-deploy-template-summary.md`: files, the exact command Odin
Prime should run, expected duration, anything deferred.
