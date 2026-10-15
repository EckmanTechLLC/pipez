# Task 10 summary: pipez hosted on VM 9090

## Files
- infra/pve/make-pipez-vm.sh (new, executable) - clone 9000 -> 9090 `pipez`, config, start, IP via guest agent (`qm guest cmd`), pool add, python3.12/git/rsync install over SSH as etl, writes pve/pipez-vm.env
- infra/deploy-pipez.sh (new, executable) - rsync, .env install with overrides, key copy, venv, systemd unit, health wait
- infra/pve/pipez-vm.env (generated): PIPEZ_VM_IP=192.168.50.64, PIPEZ_VM_ID=9090
- infra/pve/README.md - new "The pipez VM (9090)" section
- No .gitignore change needed (pipez-vm.env is meant to be tracked).

## Decisions / deviations
- Task did not list `pipez_ed25519.pub` among deploy copies; the first live check failed (sandbox.py reads SSH_KEY + ".pub"), so deploy also copies the .pub (mode 644). Pool was clean after that failed attempt.
- `.env` is also excluded from the rsync (installed separately, with overrides, mode 600; values never printed). Overrides are removed-then-appended, so re-runs do not accumulate duplicates.
- Wait for the VM uses the guest agent IP; cloud-init awaited over SSH (`cloud-init status --wait`). Total create time 4m43s.
- Host quirk: apt on the cloned base showed needrestart output (services outdated); harmless.

## Verification
1. qm config 9090: name pipez, cores 4, memory 8192, cpu host, scsi0 40G; `pvesh get /pools/pipez` lists qemu/9090 - met.
2. From this box: GET /api/runs -> 200; `grep -ci pipez` on GET / -> 3; systemctl is-active pipez -> active - met.
3. Live check on the VM (/opt/pipez/venv/bin/python, PYTHONPATH=/opt/pipez/app): vmid 9100, ip 192.168.50.77, clone->ssh-ready 31.2 s; `claude --version` rc 0 "2.1.197 (Claude Code)"; teardown ok; pool members afterwards [9090] only; qm list shows no 91xx - met.
4. bash -n on both scripts ok; deploy-pipez.sh run three times, all succeeded (idempotent) - met. make-pipez-vm.sh re-run (refusal path) not exercised.

## Left alone
VMs 9000/9001 and others; dev instance on 8400 here; pipez/, ui/, tests/; no commit. Sandbox 9100 created and destroyed via the token from the VM (root only used for host/VM setup and read-only checks).
