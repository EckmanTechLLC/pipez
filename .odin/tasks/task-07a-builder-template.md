# Task 07a: `pipez-builder` VM template on Proxmox (.19)

## Objective
Create template VM **9001 `pipez-builder`** on the Proxmox host `etl-pve`
(192.168.50.19) from template 9000 `ubuntu-base`, baked with the toolchain the
coding sandboxes need (Python 3.12 + venv with `claude-agent-sdk`, Node 20,
Claude Code CLI, Docker, git, build tools), so that per-run sandboxes are
linked clones that boot in seconds and do not reinstall anything. Everything is
scripted under `infra/pve/` and executed from this box over SSH as
`root@192.168.50.19` (already works non-interactively). Prove the result by
cloning a throwaway sandbox **through the scoped pipez API token**, SSHing into
it with pipez's own key, running `claude --version`, and destroying it.

## Reference (read first)
- Host facts (verified): Proxmox 9.2, node `etl-pve`, storage `local-lvm` (thin) + `local` (dir, snippets at `/var/lib/vz/snippets/`), bridge `vmbr0`, DHCP. Template 9000: cloud-init, `ciuser etl`, `cicustom vendor=local:snippets/base-vendor.yaml`, `agent: enabled=1`, 2 cores / 4 GB / 20 G scsi0, serial console. VMIDs in use: 101,110,120,130,140,150,160,9000. Pool `pipez` exists.
- Read `base-vendor.yaml` on the host first (`ssh root@192.168.50.19 cat /var/lib/vz/snippets/base-vendor.yaml`) — the new snippet must keep its guest-agent/ttyd/zellij behaviour.
- `/home/etl/projects/pipez/.env` holds `PIPEZ_PVE_URL`, `PIPEZ_PVE_TOKEN_ID`, `PIPEZ_PVE_TOKEN_SECRET`, `PIPEZ_PVE_NODE`, `PIPEZ_PVE_POOL`, `PIPEZ_PVE_TEMPLATE=9001`, `PIPEZ_PVE_VMID_RANGE=9100-9199`, `PIPEZ_PVE_VERIFY_TLS=0`. **Never print the secret.** The token has PVEVMAdmin-minus-migrate on `/pool/pipez`, clone rights on 9000/9001, allocate on both storages, SDN.Use on vmbr0; it gets 403 on node-level endpoints — list VMs via `/pools/pipez`.
- Proxmox API: `POST /nodes/{node}/qemu/{vmid}/clone` (`newid, name, pool, full=0`), `PUT /nodes/{node}/qemu/{vmid}/config` (`sshkeys` must be URL-encoded, `ciuser`, `ipconfig0`), `POST .../status/start`, `GET .../agent/network-get-interfaces`, `POST .../status/stop`, `DELETE /nodes/{node}/qemu/{vmid}?purge=1`, `GET /nodes/{node}/tasks/{upid}/status` to wait on async tasks.

## Deliverables
1. **`infra/pve/pipez-builder-vendor.yaml`** — cloud-config used ONCE to bake 9001. Keeps everything `base-vendor.yaml` does, plus: `build-essential pkg-config python3.12 python3.12-venv python3-pip ca-certificates gnupg jq`; Node 20 from NodeSource; Docker CE from the official apt repo with `etl` in the `docker` group; `npm install -g @anthropic-ai/claude-code`; a venv at `/opt/pipez/venv` owned by `etl` with `anthropic` and `claude-agent-sdk` installed; `unattended-upgrades` disabled (avoids apt locks at sandbox boot); a marker file `/etc/pipez-builder-version` containing the build date and tool versions (`claude --version`, `node --version`, `python3.12 --version`, `docker --version`).
2. **`infra/pve/pipez-sandbox-vendor.yaml`** — the LIGHT cloud-config that 9001 carries when converted to a template, so clones do not re-run installs: enable `qemu-guest-agent` and `ttyd`, `package_update: false`, nothing else. Clones still get per-VM user-data (ciuser/sshkeys/ipconfig) from Proxmox.
3. **`infra/pve/make-builder-template.sh`** (bash, `set -euo pipefail`, run from this box; all host work via `ssh root@192.168.50.19`): generates pipez's SSH keypair at `~/.ssh/pipez_ed25519` if absent; copies both snippets to the host; refuses if VMID 9001 exists unless `--force` (then destroys it); `qm clone 9000 9001 --name pipez-builder --full 1`; `qm set 9001 --cores 4 --memory 8192 --cicustom vendor=local:snippets/pipez-builder-vendor.yaml --ciuser etl --ipconfig0 ip=dhcp --sshkeys <file with root@etl-pve's key + pipez's public key>`; `qm disk resize 9001 scsi0 40G`; start; poll `qm agent 9001 ping` then `qm agent 9001 exec -- cloud-init status --wait` (with a 20-minute ceiling and progress lines); verify inside the guest via `qm agent exec`: python3.12, node, docker, claude, `/opt/pipez/venv/bin/python -c "import claude_agent_sdk"`; then `cloud-init clean --logs --machine-id`, `apt-get clean`, shutdown; `qm set 9001 --cicustom vendor=local:snippets/pipez-sandbox-vendor.yaml`; `qm template 9001`. Idempotent re-runs with `--force` only.
4. **`infra/pve/smoke-sandbox.sh`** — uses ONLY the API token from `.env` (curl + jq; `-k` when `PIPEZ_PVE_VERIFY_TLS=0`): picks the first free VMID in `PIPEZ_PVE_VMID_RANGE` not present in `/pools/pipez`, linked-clones 9001 into it (`pool=pipez`, name `pipez-smoke-<ts>`), sets `sshkeys` to pipez's public key, starts it, waits for the guest agent and an IPv4 on `eth0`/`ens*` via `agent/network-get-interfaces` (≤ 3 min), SSHes as `etl` with `~/.ssh/pipez_ed25519` (`StrictHostKeyChecking=no`, `UserKnownHostsFile=/dev/null`) and runs `claude --version && docker ps && /opt/pipez/venv/bin/python -c "import claude_agent_sdk; print('sdk ok')" && cat /etc/pipez-builder-version`, then stops and destroys the VM (`purge=1`) and waits for the destroy task. Prints elapsed time for clone→SSH-ready. Exits non-zero if any step fails, and still attempts cleanup.
5. **`infra/pve/README.md`** — what 9001 is, how it was built, how to rebuild (`--force`), the two snippets' roles, the smoke test, VMID conventions (9001 template; 9100–9199 sandboxes; 9200–9299 reserved for deployed apps), and the token's scope.
6. **Run it.** Execute `make-builder-template.sh`, then `smoke-sandbox.sh`. Record both outputs (trimmed; no secrets) in the summary.

## Out of scope
- Any change under `pipez/`, `ui/`, `tests/` (another agent is editing those).
- Windows templates. Deploy-target VMs (task 09/10). The pipez-side Python sandbox client (07b).
- Touching VMs other than 9001 and the smoke clone you create. Never modify 9000 or any existing VM.
- Committing to git.

## Verification criteria
1. `ssh root@192.168.50.19 qm config 9001` shows `template: 1`, `name: pipez-builder`, `cicustom: vendor=local:snippets/pipez-sandbox-vendor.yaml`, 4 cores, 8192 MB, 40G disk.
2. `smoke-sandbox.sh` succeeds end-to-end using the token (no root SSH to the host inside it), prints the tool versions from inside the clone, and leaves `/pools/pipez` with no members afterwards (`qm list` on the host shows no 91xx VM).
3. `~/.ssh/pipez_ed25519{,.pub}` exist with mode 600/644.
4. `bash -n` on both scripts passes; `python3 -c "import yaml,sys; [yaml.safe_load(open(f)) for f in sys.argv[1:]]" infra/pve/*.yaml` passes (install PyYAML into `.venv` only if needed for this check, not system-wide).
5. `git status` shows `infra/` untracked and nothing else new from this task.

## Write summary
MANDATORY, last step: `.odin/sessions/task-07a-builder-template-summary.md`: files, the baked tool versions, clone→SSH-ready seconds from the smoke test, trimmed outputs, and anything about the host that surprised you.
