# Task 07b-a (hotfix): PveSandbox live provisioning fails

## Objective
The first live `PveSandbox.provision()` against template 9001 failed:
`set_config` → `PVE 400: Parameter verification failed.`, then the cleanup
`destroy` → `PVE 403: Permission check failed (/vms/9100, VM.Allocate)`.
The shell smoke test (`infra/pve/smoke-sandbox.sh`) succeeds with the same
token, so this is the Python client. Fix it and prove it live.

## Reference
- `pipez/pve.py` (`_req` form-encodes every field once with `quote(..., safe="")`; `clone`, `wait_task`, `set_config`, `destroy`), `pipez/sandbox.py` (`PveSandbox.provision/teardown`)
- `infra/pve/smoke-sandbox.sh` lines 50–57: the working sequence. Note `sshkeys=$(jq -rn --arg k "$(cat "$KEY.pub")" '$k|@uri')` **then** `--data-urlencode` — the sshkeys value is URL-encoded *as a value* and then form-encoded again. `wait_task` there polls `tasks/{upid}/status` until `status == "stopped"` and checks `exitstatus == "OK"`.
- `.odin/sessions/task-07a-builder-template-summary.md` "Deviations"/"fixes" (sshkeys encoding; `qm guest exec`).

## Deliverables
1. `set_config`: URL-encode the `sshkeys` value (`quote(value, safe="")`) before it goes through the normal form encoding; keep other fields as-is. Add a unit test asserting the encoded body contains the double-encoded key (e.g. `%2520` for a space is NOT expected — the key has spaces → `+`/`%20` once, then `%2520`? Work it out against what the shell script sends: `@uri` encodes space as `%20`, `--data-urlencode` then encodes `%` as `%25` → body contains `%2520`). Match the shell script exactly.
2. `wait_task`: raise `PveError` if the finished task's `exitstatus` is not `"OK"` (include the exitstatus text); surface the Proxmox error message from the 400/403 response body (`errors`/`message` fields) in `PveError` instead of just the reason phrase.
3. `PveSandbox.provision` cleanup path: on failure after clone, check `status(vmid)` first; if the VM does not exist or is not in the pool, log and skip destroy rather than raising a second error that masks the first. `teardown` must never raise over a 403/404 during destroy of a VM that is already gone — log `sandbox_teardown_warning` and return.
4. **Live check** (9001 is a template now): run the equivalent of the following and paste trimmed output in the summary — provision → `run("cat /etc/pipez-builder-version && claude --version")` → `put`/`get` round-trip under `/work/<run_id>/` → `teardown(False)` → `pool_members("pipez")` is empty. Use `run_id="livecheck"`.
   ```
   .venv/bin/python -c "import asyncio; from pipez.pve import ProxmoxClient; from pipez.sandbox import PveSandbox; ..."
   ```
   If it fails, fix and re-run until it passes; always leave the pool empty (verify with `ssh root@192.168.50.19 qm list` that no 91xx VM remains — read-only use of root; destroy a leftover with the token first, root only as last resort, and say so).

## Out of scope
Everything else (no engine/UI changes; no changes under infra/). Do not touch port 8400. Do not modify VM 9000/9001.

## Verification criteria
1. `.venv/bin/python -m pytest -q tests/` passes (57 + new).
2. Live check passes and the pool is empty afterwards; clone→SSH-ready seconds reported.

## Write summary
`.odin/sessions/task-07b-a-pve-client-hotfix-summary.md` (short, with the live-check output).
