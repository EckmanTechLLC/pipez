# infra/pve - pipez sandbox template on Proxmox

Host: `etl-pve` (192.168.50.19), storage `local-lvm` + `local` (snippets), bridge `vmbr0`, DHCP.

## What 9001 is
`pipez-builder` is a Proxmox **template** cloned from `9000 ubuntu-base`
(Ubuntu 26.04, cloud-init, user `etl`). It is baked once with the toolchain
coding sandboxes need, so per-run sandboxes are linked clones that boot in
seconds and install nothing:

- Python 3.12 (deadsnakes PPA; 26.04 ships only 3.14). Both `python3` (3.14) and `python3.12`
  have venv + pip (`python3-venv python3-pip` installed). Plus a venv at `/opt/pipez/venv`
  (owned by `etl`) with `anthropic` and `claude-agent-sdk`
- Node 20 (NodeSource), Claude Code CLI (`npm i -g @anthropic-ai/claude-code`)
- Docker CE (official repo), `etl` in the `docker` group
- git, build-essential, pkg-config, jq, zellij + ttyd (as in `base-vendor.yaml`)
- unattended-upgrades removed (no apt locks at sandbox boot)
- `/etc/pipez-builder-version` records build date and tool versions
- 4 cores, 8 GB RAM, 40 G disk, CPU type `host` (required: the Claude Code
  binary hangs on the default `kvm64` CPU, which lacks AVX/SSE4)

## Files
- `make-builder-template.sh [--force]` - builds 9001 from this box over `ssh root@192.168.50.19`.
  Creates `~/.ssh/pipez_ed25519` if missing, copies snippets, clones 9000, boots with the
  builder snippet, waits for cloud-init (20 min ceiling), verifies tools in-guest, cleans
  cloud-init state, swaps in the sandbox snippet, converts to a template. Refuses if 9001
  exists unless `--force` (which destroys it first).
- `pipez-builder-vendor.yaml` - heavy cloud-config, used ONCE during the bake.
- `pipez-sandbox-vendor.yaml` - light cloud-config the finished template carries
  (enable guest agent + ttyd, `package_update: false`). Clones still get per-VM
  user-data (ciuser / sshkeys / ipconfig) from Proxmox.
- `smoke-sandbox.sh` - end-to-end proof using ONLY the API token from `.env`.

## Rebuild
    infra/pve/make-builder-template.sh --force

## Smoke test
    infra/pve/smoke-sandbox.sh
Picks the first free VMID in `PIPEZ_PVE_VMID_RANGE` not in `/pools/pipez`, linked-clones
9001 (pool `pipez`), injects pipez's public key, starts, waits for an IPv4 via the guest
agent, SSHes in as `etl`, runs `claude --version`, `docker ps`, the SDK import and prints the
marker file, then stops and destroys the clone. Prints clone-to-SSH-ready seconds.

## pipez-app-base (9002)
Deploy VMs (`pipez-app-<run>`, 9200-9299) used to clone plain 9000, whose vendor snippet
re-ran `apt` on every clone, so the guest agent (how pipez learns the IP) appeared only
after >240 s. 9002 bakes that in once: `qemu-guest-agent`, python3, python3-venv,
python3-pip, curl, ca-certificates, rsync, gnupg. No zellij/ttyd.
- `make-app-template.sh [--force]` - clones 9000 -> 9002 (2 cores, 2 GB), boots with
  `pipez-app-vendor.yaml` (bake-time), waits for cloud-init, verifies the agent and
  `python3 -m venv`, cleans cloud-init state, swaps in `pipez-app-clone-vendor.yaml`
  (installs nothing, just enables the agent), converts to a template. Refuses if 9002
  exists unless `--force`. Never touches 9000/9001/9090 or 91xx/92xx VMs.
- Rebuild: `infra/pve/make-app-template.sh --force`
- Use it: set `PIPEZ_PVE_DEPLOY_TEMPLATE=9002` in `.env`.

## VMID conventions
- 9000 ubuntu-base, 9001 pipez-builder, 9002 pipez-app-base (templates)
- 9100-9199 per-run sandboxes (linked clones)
- 9200-9299 reserved for deployed apps

## API token
`pipez@pve!builder` (`PIPEZ_PVE_TOKEN_*` in `.env`): PVEVMAdmin minus migrate on
`/pool/pipez`, clone rights (PVETemplateUser) on /vms/9000 and /vms/9001 (the 9001 ACL is added by make-builder-template.sh), allocate on `local-lvm` and `local`, SDN.Use on
`vmbr0`. Node-level endpoints return 403: list VMs via `/pools/pipez`, not `/nodes/*/qemu`.

## The pipez VM (9090)
The instance you actually use runs on VM 9090 `pipez` (full clone of 9000, 4 cores, 8 GB,
40 G, CPU `host`, user `etl`, DHCP, in pool `pipez` so the token can see it; 9090 is outside
the 9100-9199 sandbox and 9200-9299 app ranges). Dev stays on the dev box.

- Create: `infra/pve/make-pipez-vm.sh [--force]` (root SSH to the host). Refuses if 9090 exists
  unless `--force`. Installs python3.12 (deadsnakes), git, rsync; creates `/opt/pipez` owned by
  `etl`; writes the IP to `infra/pve/pipez-vm.env` (`PIPEZ_VM_IP`, `PIPEZ_VM_ID`). DHCP lease
  may change after a long outage: update that file (or re-run the script).
- Deploy: `infra/deploy-pipez.sh` (from the dev box, idempotent). rsyncs the tree to
  `/opt/pipez/app/` (`--delete`; excludes .venv, .git, db, logs, artifacts), installs `.env`
  (mode 600) plus overrides `PIPEZ_HOST=0.0.0.0`, `PIPEZ_PORT=8400`, `PIPEZ_DB`,
  `PIPEZ_ARTIFACTS_DIR`, copies `~/.ssh/pipez_ed25519{,.pub}` to the VM (needed to SSH into
  sandboxes), updates `/opt/pipez/venv`, installs/restarts the `pipez` systemd unit and waits
  for `/api/runs`.
- Logs: `ssh etl@<ip> journalctl -u pipez -f`; status: `systemctl status pipez`.
- Data: `/opt/pipez/data/pipez.sqlite3` and `/opt/pipez/data/artifacts/` (never touched by deploy).
- Roll back: check out the older tree somewhere and run its `infra/deploy-pipez.sh`
  (it needs `infra/pve/pipez-vm.env`; copy it over). The data dir is kept.
