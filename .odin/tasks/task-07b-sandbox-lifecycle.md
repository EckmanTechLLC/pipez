# Task 07b: Proxmox client + per-run sandbox lifecycle (no code execution yet)

## Objective
Give pipez the ability to own a sandbox VM per run: pick a free VMID, linked-clone
the `pipez-builder` template through the scoped API token, inject pipez's SSH
key, boot, discover the IP via the guest agent, run commands and copy files over
SSH, and tear down (destroy on success, keep on failure). Everything is behind a
`Sandbox` interface with a `MockSandbox` that uses local temp directories so the
whole engine still runs offline. This task does NOT run coding agents or touch
`_execute` — 07c does. It wires lifecycle into the run: a sandbox is provisioned
when a plan is approved and torn down when the run finishes.

## Reference (read first)
- `.odin/project-index.md`; summaries for 06b (`_materialise_plan`, run phases, `_run_resume`), 02 (`providers.py` uses `import httpx2 as httpx` + `MockTransport` tests), 07a if present (`infra/pve/smoke-sandbox.sh` shows the exact API call sequence that works against this host; `infra/pve/README.md`)
- `/home/etl/projects/pipez/.env` keys: `PIPEZ_PVE_URL, PIPEZ_PVE_TOKEN_ID, PIPEZ_PVE_TOKEN_SECRET, PIPEZ_PVE_NODE, PIPEZ_PVE_POOL, PIPEZ_PVE_TEMPLATE, PIPEZ_PVE_VMID_RANGE, PIPEZ_PVE_VERIFY_TLS`. Never log or print the secret.
- Token scope (verified): full VM admin inside pool `pipez`; clone on 9000/9001; 403 on node-level endpoints — enumerate VMs via `GET /pools/{pool}`, not `/nodes/{node}/qemu`.
- Proxmox API (token header `Authorization: PVEAPIToken=<id>=<secret>`): `POST /nodes/{node}/qemu/{tmpl}/clone {newid,name,pool,full=0}` → UPID; `GET /nodes/{node}/tasks/{upid}/status` until `status == "stopped"` (check `exitstatus == "OK"`); `PUT /nodes/{node}/qemu/{vmid}/config {sshkeys=<urlencoded>, ciuser, ipconfig0}`; `POST .../status/start`; `GET .../agent/network-get-interfaces` (404/500 until the agent is up — retry); `POST .../status/stop`; `DELETE /nodes/{node}/qemu/{vmid}?purge=1&destroy-unreferenced-disks=1`; `GET .../status/current`.
- SSH: user `etl`, key `~/.ssh/pipez_ed25519` (created by 07a; create it if absent with `ssh-keygen -t ed25519 -N "" -C pipez`), options `-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o BatchMode=yes -o ConnectTimeout=10`.

## Design (binding)
- `pipez/pve.py`: `class ProxmoxClient` (async, `httpx2`), constructed from `config`, with `clone(template, newid, name, pool) -> upid`, `wait_task(upid, timeout)`, `set_config(vmid, **fields)`, `start(vmid)`, `stop(vmid)`, `destroy(vmid)`, `status(vmid)`, `agent_interfaces(vmid)`, `pool_members(pool) -> [vmid]`, `free_vmid(range_str, taken) -> int`. Accepts an optional `transport` for tests. Raises `PveError(status, message)`; 5xx and transport errors are `PveTransientError(PveError)` and retried 3× with short backoff.
- `pipez/sandbox.py`: `class Sandbox` (abstract): `async provision()`, `async run(cmd: str, *, cwd=None, env: dict=None, stdin: str=None, timeout=600) -> {rc, stdout, stderr, seconds}`, `async put(path, content: bytes|str, mode=0o644)`, `async get(path) -> bytes`, `async teardown(keep: bool)`, properties `vmid, ip, workdir`. `PveSandbox(run_id, client)`: provision = free vmid → clone (`name=pipez-<run_id>`) → wait → set_config (sshkeys = pipez pubkey, ciuser etl) → start → poll agent interfaces for an IPv4 that is not loopback (≤ `PIPEZ_SANDBOX_BOOT_TIMEOUT`, default 240 s) → wait until `ssh true` succeeds → `mkdir -p /work/<run_id>`. `run` uses `asyncio.create_subprocess_exec("ssh", ...)`; `env` is passed by prefixing `env K=V ...` **except** any key ending in `_KEY`/`SECRET`/`TOKEN`, which is written to `/work/<run_id>/.env` (mode 600) once and sourced by the command instead, so secrets never appear in `ps` or logs. `put/get` use `ssh ... 'cat > path'` / `'cat path'` (no scp dependency). `teardown(keep=False)` stops then destroys and waits; `keep=True` leaves it running and records that. `MockSandbox(run_id)`: `provision` makes a temp dir, `run` executes with `bash -lc` locally in that dir (same return shape), `put/get` are file ops, `teardown` removes the dir unless keep. `make_sandbox(run_id)` returns Mock when `config.MOCK` or when `PIPEZ_SANDBOX=mock`, else Pve.
- Persistence: table `sandboxes(run_id TEXT PRIMARY KEY, kind TEXT, vmid INTEGER, name TEXT, ip TEXT, status TEXT, created_at REAL, ready_at REAL, destroyed_at REAL, error TEXT, kept INTEGER DEFAULT 0)`; statuses `provisioning|ready|destroying|destroyed|kept|failed`. `db.upsert_sandbox`, `db.get_sandbox(run_id)`, `db.list_sandboxes(status=None)`.
- Engine wiring: after `_materialise_plan` sets `running`, provision the sandbox before executing the tree (`run.status` shows `provisioning` while it happens; add that state); store it on the engine as `self._sandboxes[run_id]`; on run completion `teardown(keep = status != "done")` — destroy on success, keep on failure (log `sandbox_kept` with vmid/ip so the human can SSH in). `engine.resume()` for a run with a `kept` sandbox reuses it if `status(vmid)` is running, otherwise provisions a new one. New API: `GET /api/sandboxes` (all rows), `POST /api/sandboxes/{run_id}/destroy` (manual gc of a kept one), `GET /api/runs/{id}/sandbox`. SSE: `sandbox` event on each status change; snapshot includes `sandbox` per run.
- Config: `PIPEZ_SANDBOX` (`pve|mock`, default `pve` unless `MOCK`), `PIPEZ_SANDBOX_BOOT_TIMEOUT` (240), `PIPEZ_SSH_KEY` (default `~/.ssh/pipez_ed25519`), `PIPEZ_SANDBOX_KEEP_ON_FAILURE` (1).

## Deliverables
1. `pipez/pve.py` with the client above + `tests/test_pve.py` using `httpx.MockTransport`: clone→UPID→wait polling, URL-encoded `sshkeys` in `set_config`, `free_vmid` skipping pool members and honouring the range, 5xx retry then `PveTransientError`, 403 → `PveError` (no retry).
2. `pipez/sandbox.py` with `Sandbox`, `PveSandbox`, `MockSandbox`, `make_sandbox`; `tests/test_sandbox.py` for `MockSandbox` (`run` returns rc/stdout, `put`/`get` round-trip, secret env goes to `.env` not the command line — assert by inspecting the composed command string via a hook, teardown keep/destroy) and for `PveSandbox.provision` against a fake client (ordering of calls, IP discovery retry, boot timeout → `SandboxError`).
3. `db.py` table + helpers; `config.py` keys; `.env.example` additions.
4. Engine wiring + `provisioning` state + teardown policy + resume reuse; `server.py` routes + SSE; `tests/test_plan.py` extended: approved mock run goes `provisioning → running → done` and `GET /api/runs/{id}/sandbox` shows `destroyed`; a run forced to fail (stub LLM) leaves the sandbox `kept`.
5. UI: run status colour for `provisioning`; a one-line sandbox badge on the run header (`vmid · ip · status`) with a **destroy** button when `kept`. Keep it minimal.
6. README "Sandboxes" section; `.odin/project-index.md`.

## Out of scope
- Running the Agent SDK / any coding inside the VM; worktrees; merging (07c).
- Deploy-target VMs (09/10). Windows.
- Live verification against Proxmox if template 9001 does not exist yet — in that case say so in the summary; the controller runs the live check later.
- Committing to git.

## Verification criteria
1. `.venv/bin/python -m pytest -q tests/` passes (35 prior + new).
2. Mock-mode server smoke (`PIPEZ_MOCK=1`, port **8401** — 8400 is in use by a live instance, do not touch it): submit → accept_all → approve → run goes `provisioning` → `running` → `done`; `/api/sandboxes` shows one `destroyed` row with `kind: mock`. Stop 8401 with `fuser -k 8401/tcp`; clean temp paths.
3. If `ssh root@192.168.50.19 qm config 9001` shows `template: 1`, run a live check with a tiny script using `ProxmoxClient` + `PveSandbox` directly (not through the engine): provision, `run("cat /etc/pipez-builder-version")`, teardown(keep=False); confirm `/pools/pipez` has no members afterwards. Otherwise write "live check deferred: 9001 absent".
4. `grep -rn "PVE_TOKEN_SECRET" pipez/ | grep -v config.py` → nothing (the secret is read in config only and never logged).
5. `node --check` on the inline script still passes.

## Write summary
MANDATORY, last step: `.odin/sessions/task-07b-sandbox-lifecycle-summary.md`: files, trimmed verification output, live-check result or deferral, ambiguities resolved.
