# Task 07b summary

## Files
- pipez/pve.py (new): ProxmoxClient, PveError, PveTransientError.
- pipez/sandbox.py (new): Sandbox, PveSandbox, MockSandbox, compose_command, make_sandbox, SandboxError.
- pipez/db.py: `sandboxes` table; upsert_sandbox / get_sandbox / list_sandboxes.
- pipez/config.py: PVE_*, SANDBOX, SANDBOX_BOOT_TIMEOUT, SSH_KEY, SANDBOX_KEEP_ON_FAILURE. .env.example updated.
- pipez/engine.py: `sandbox_factory` ctor arg, `provisioning` state, _run_with_sandbox / _acquire_sandbox / _release_sandbox / destroy_sandbox; answer_plan(approve) and resume() of planned runs go through it.
- pipez/server.py: GET /api/sandboxes, GET /api/runs/{id}/sandbox, POST /api/sandboxes/{run_id}/destroy; SSE `sandbox` event + snapshot.
- ui/index.html: provisioning colour, sandbox badge (vmid · ip · status) + destroy button when kept.
- tests: new test_pve.py (8), test_sandbox.py (8), test_plan.py (+3, one assertion changed to `provisioning`), conftest.py autouse fixture forcing mock sandboxes.
- README "Sandboxes"; .odin/project-index.md.

## Decisions / interpretations
- Secret kept in config only as `PVE_AUTH_HEADER` (a ready header value), so no other module mentions PVE_TOKEN_SECRET.
- "Retried 3x": first try + 3 retries (4 calls) with backoff 0.5s, 1s, 2s.
- sshkeys body is form-encoded with %20 (not +), matching the working curl --data-urlencode sequence.
- A failed provision after clone best-effort destroys the VM (no leak); provision failure fails the run and marks the sandbox row `failed`.
- In-process `_reserved` vmid set avoids two concurrent provisions picking the same free VMID.
- Resume only provisions a sandbox for runs that have a plan; legacy/test runs without one resume as before. A kept sandbox is reused if `alive()` (pve: status running; mock: dir exists), else replaced (`sandbox_replaced` logged; old VM not auto-destroyed).
- Manual destroy allowed for `kept` and `failed` rows only.
- MockSandbox.provision sleeps 0.2s so `provisioning` is observable.
- Mock sandbox directory is stored in the `name` column so a kept mock can be re-adopted.
- Existing test assertion in test_plan (status right after approve) changed from running to provisioning, as required by the new state.

## Verification
1. pytest: 54 passed (35 prior + 19 new). Met.
2. Mock smoke on 8401 (temp DB): statuses seen provisioning, running, rendering, done; /api/sandboxes one row, status destroyed, kind mock. Server stopped via fuser, temp dir removed, no leftover /tmp/pipez-sandbox-*. Met.
3. Live check: DEFERRED. `qm config 9001` has no `template: 1` line (it is still a plain VM named pipez-builder). Not run. /pools/pipez currently empty (checked).
4. grep PVE_TOKEN_SECRET in pipez/ outside config.py: nothing. Met.
5. node --check on the extracted inline script: ok. Met.

## Notes
- An early test run (before conftest forced mock) hit the real PVE once through the default factory; provisioning failed, a destroy of vmid None got 403, and the pool was verified empty afterwards. Guard added (no vmid -> SandboxError) and tests can no longer reach Proxmox.
- Not touched: infra/, VM 9000/9001, port 8400 instance, git.
- Not exercised: the UI in a browser; real SSH paths of PveSandbox (run/put/get) beyond command composition.
