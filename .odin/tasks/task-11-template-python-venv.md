# Task 11 — Builder template: make `python3 -m venv` work

## Objective
The first real run (c6973d74ac0e on the VM instance) failed at scaffold with
`python3 -m venv .venv` → "ensurepip is not available … apt install python3.14-venv".
On template 9001, `python3` is Ubuntu 26.04's 3.14 and only `python3.12-venv` (deadsnakes)
was installed. Fix the builder image so the system `python3` has venv + pip, rebake 9001,
and prove it from a throwaway clone. Sandbox 9100 (the only linked clone) has already
been destroyed; pool `pipez` has no 91xx members.

## Reference
- `infra/pve/pipez-builder-vendor.yaml` — bake script (lines ~59–62 are the Python block)
- `infra/pve/make-builder-template.sh` — rebuild script; `--force` destroys and rebuilds 9001
- `infra/pve/smoke-sandbox.sh` — clone→SSH smoke test against 9001
- `infra/pve/README.md`
- `.odin/sessions/task-07b-*-summary.md` — how the template was built originally (gotchas: `--cpu host`, ACL re-grant)

## Deliverables
1. In `pipez-builder-vendor.yaml`, alongside the deadsnakes install, also install
   `python3-venv python3-pip` (the Ubuntu 26.04 packages for the system 3.14) so that
   `python3 -m venv x && x/bin/pip --version` works. Keep python3.12 + python3.12-venv.
2. In `make-builder-template.sh` step 6 (verify), add a check:
   `python3 -m venv /tmp/vt && /tmp/vt/bin/pip --version && rm -rf /tmp/vt`.
3. Run `bash infra/pve/make-builder-template.sh --force` from this box (root SSH to
   192.168.50.19 is already set up). Wait for it to finish; it takes ~10 min. Do NOT touch
   VM 9000, 9090, or any VM not in the 9001/91xx range.
4. Run `bash infra/pve/smoke-sandbox.sh` (or equivalent: linked-clone 9001 into 91xx, SSH
   as etl, run `python3 -m venv /tmp/vt && /tmp/vt/bin/pip --version`, `python3.12 --version`,
   `node --version`, `claude --version`), then destroy the clone. Pool `pipez` must end
   with no 91xx members.
5. Note in `infra/pve/README.md` that `python3` (3.14) and `python3.12` both have venv/pip.

## Out of scope
- Any change to `pipez/` Python code, prompts, DB, or the UI.
- Resuming run c6973d74ac0e (the user does that from the UI).
- Changing anything on the pipez VM 9090 / 192.168.50.64.
- Rewriting the bake flow; minimal edits only.

## Verification criteria
- `grep -n "python3-venv" infra/pve/pipez-builder-vendor.yaml` shows the new package line.
- `make-builder-template.sh --force` completed with "template 9001 ready" and `qm config 9001`
  shows `template: 1`.
- Smoke clone output shows pip version from a `python3 -m venv` venv.
- `pvesh get /pools/pipez` (via root SSH) lists no VMs in 9100–9199.
- ACL on `/vms/9001` for `pipez@pve!builder` is present (script re-grants it; confirm with
  `pveum acl list | grep 9001`).

## Rules
- Never print the PVE token secret or any API key.
- Never run `pkill`; never touch ports 8400/8401.
- Do not commit.

## Write summary
`.odin/sessions/task-11-template-python-venv-summary.md` — what changed, bake duration,
smoke output (pip version line), final pool state. Mandatory last step.
