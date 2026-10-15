# Task 11 summary

## Changed
- infra/pve/pipez-builder-vendor.yaml: added `apt-get install -y python3-venv python3-pip` after the deadsnakes install (python3.12 + python3.12-venv kept).
- infra/pve/make-builder-template.sh: step 6 verify list now includes `python3 -m venv /tmp/vt && /tmp/vt/bin/pip --version && rm -rf /tmp/vt`.
- infra/pve/smoke-sandbox.sh: in-guest check line now also runs the venv/pip check, `python3.12 --version`, `node --version` before the existing checks (small addition, "or equivalent" in task).
- infra/pve/README.md: noted `python3` (3.14) and `python3.12` both have venv/pip.

## Bake
- `make-builder-template.sh --force`: about 7m21s wall time, ended with "template 9001 ready".
- In-guest verify output: pip 25.1.1 (python 3.14).

## Smoke (clone 9100, destroyed afterwards)
- pip 25.1.1 from /tmp/vt/lib/python3.14/site-packages/pip (python 3.14)
- Python 3.12.15, node v20.20.2, claude 2.1.197, docker ps ok, sdk ok. Clone to SSH-ready: 33s.

## Verification criteria
- grep python3-venv in vendor yaml: met (line 64).
- "template 9001 ready" and `template: 1` in qm config: met.
- Smoke shows pip version from python3 venv: met.
- Pool pipez has no 91xx members: met (only 9090 remains; qm list shows no 91xx VMs).
- ACL on /vms/9001 for pipez@pve!builder (and pipez@pve): met, PVETemplateUser.

## Left alone
- VMs 9000, 9090, run c6973d74ac0e, all pipez/ code. Nothing committed.
