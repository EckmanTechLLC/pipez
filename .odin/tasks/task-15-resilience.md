# Task 15 — Resilience: restart recovery, real remote cancel, orphan sweep, lazy DB

## Objective
Make pipez safe to restart and stop. Today: (1) a server restart leaves in-flight runs
(`running`/`deploying`/`verifying`/`provisioning`/`planning`/`briefing`/`intake`) stuck in
their status forever, with half-built deploy VMs; (2) `stop()` cancels the asyncio task and
kills the local `ssh`, but the remote runner (Claude Agent SDK session) keeps running in the
sandbox, spending money, until the VM is destroyed; (3) Proxmox VMs in the `pipez` pool that
the DB no longer tracks (or tracks as live when they are gone) are never reconciled;
(4) `pipez/server.py` opens the SQLite DB at import time (`db = Database(config.DB_PATH)`),
so importing the module (tests, tooling) creates a stray `pipez.sqlite3` in the CWD.
Policy chosen by the user: on restart, mark interrupted and let the human resume — never
auto-resume.

## Reference
- `pipez/server.py`: module-level `app`/`db`/`prefs`/`bus`/`engine`, `@app.on_event("startup")`
  (~25–40), route handlers use the module globals.
- `pipez/engine.py`: `__init__` (~68), `_spawn`/`_run_tasks`/`_alive` (~168–185), `stop()`
  (~773), `_mark_cancelled`, `_abort_deploy` (~920), `_destroy_deploy_vm` (~913),
  `ACTIVE_DEPLOY` (~870), `_acquire_sandbox` (~1023, adopts kept rows), `_release_sandbox`,
  `_sandbox_row`, `_execute_code_task` runner invocation (~1890–1910: `sb.run(cmd, cwd=wt,
  env=env, timeout=LEAF_TIMEOUT)`), `resume()` (~699: already handles `failed` runs with a
  plan → `_run_with_sandbox`, reuses a kept sandbox).
- `pipez/sandbox.py`: `_spawn` (kills local child on cancel), `compose_command`, `Sandbox.run`,
  `PveSandbox._ssh/_exec/alive/teardown/adopt`, `MockSandbox`.
- `pipez/deploy.py`: `MockDeploySandbox` (pidfile pattern, "adopted after a restart"),
  `make_deploy_sandbox`, `Deployer`.
- `pipez/pve.py`: `ProxmoxClient.pool_members`, `status`, `stop`, `destroy`.
- `pipez/db.py`: `list_sandboxes(status=)`, `list_deployments`, `get_runs`, `set_run_status`,
  `finish_run`, `upsert_deployment`, `task_log`.
- `pipez/config.py`: `SANDBOX_KEEP_ON_FAILURE`, `PVE_*`, `SANDBOX`.
- Tests: `tests/test_stop.py`, `tests/test_deploy.py` (`dep` fixture), `tests/test_sandbox.py`,
  `tests/test_pve.py` (fake PVE client pattern), `tests/conftest.py`.
- `.odin/sessions/task-08-control-and-fix-loops-summary.md` (stop semantics),
  `task-09-deploy-to-vm-summary.md`, `task-07b-*-summary.md` (sandbox lifecycle).

## Deliverables
1. **Lazy server state.** `server.py` builds `db`, `prefs`, `bus`, `engine` inside startup (or a
   `create_app()` factory / lifespan), not at import. Importing `pipez.server` must not touch
   the filesystem. Routes keep working (use a module-level holder or `app.state`). Delete the
   stray repo-root `pipez.sqlite3*` only if it is tracked by nothing and gitignored (it is).
2. **Startup recovery** — `Engine.recover()` called once at startup, before serving:
   - Runs in `intake`/`briefing`/`planning`/`provisioning`/`running`/`deploying`/`verifying`
     with no live driver → active task rows → `cancelled` (error "interrupted by server
     restart"), run → `failed` with the same error (`awaiting_human`/`awaiting_plan`/terminal
     runs untouched). Log `run:<id>` event `interrupted` {was: <status>}. Resume must work
     from this state (it already treats `failed` + plan + kept sandbox as resumable; confirm
     with a test and fix if not — e.g. a run interrupted in `planning` must go back through
     `_plan`, one interrupted in `intake` back through `_intake`).
   - Sandboxes with status `provisioning`/`ready` and no live run → if the VM is alive
     (`sb.alive()` after `adopt`), mark `kept` (so resume reuses it); if gone, mark
     `destroyed`. Deploy VMs with deployment status `provisioning`/`installing`/`starting` →
     destroy the VM, deployment `failed` ("interrupted by server restart"). `ready`
     deployments are left alone.
   - Never auto-resume.
3. **Real remote cancel.** The runner is started so it can be killed from outside: the engine
   wraps the runner command as `setsid bash -c '<cmd>' & echo $! > <wd>/run/<task_id>.pid; wait`
   (or equivalent that records the process-group id and still returns the runner's rc and
   stdout). On `CancelledError` in `_execute_code_task` (stop) — and in `recover()` for
   sandboxes that are alive — kill the recorded group (`kill -TERM -- -<pgid>`, then `-KILL`
   after 5 s) via a fresh `sb.run` (short timeout, best effort, shielded from cancellation
   with `asyncio.shield`). Same for the reviewer and fixer leaves (they go through the same
   path). Mock sandbox: a `killed` list the tests can assert on.
4. **Orphan sweep** — `Engine.sweep()` at startup after `recover()` and via
   `POST /api/sandboxes/sweep`: list pool members (vmids in `PVE_VMID_RANGE` and
   `DEPLOY_VMID_RANGE`); for each VM not matching a DB row that is `kept`/`ready`/`provisioning`
   (sandboxes) or `ready`/active (deployments) → log it and, if `config.SWEEP_DESTROY`
   (`PIPEZ_SWEEP_DESTROY`, default 0), destroy it; otherwise only report. Return/log the list
   `{vmid, name, action}`. Rows claiming live VMs that do not exist → marked `destroyed`.
   Never touch vmids outside the two ranges (9090/9000/9001 are out of range by construction;
   assert it anyway). Mock/PVE-less configs: sweep is a no-op returning [].
5. **Tests**: (a) `import pipez.server` creates no file; (b) recover(): a `running` run with
   a mock sandbox → run `failed`/"interrupted…", tasks cancelled, sandbox `kept`, resume then
   completes; (c) recover(): `deploying` with MockDeploySandbox mid-install → deployment
   `failed`, VM torn down; (d) stop during a leaf → the mock sandbox records a kill of the
   runner's pid group; (e) sweep with a fake PVE client: an unknown 91xx VM is reported (and
   destroyed only when `SWEEP_DESTROY`), a 9000-range VM is never considered, a `kept` row
   whose VM is gone becomes `destroyed`.

## Out of scope
- Auto-resume; UI changes beyond nothing (API only); planner/intake/prompts/review logic;
  builder template; the deploy recipe; committing; touching the live VM (Odin Prime deploys).

## Verification criteria
- `grep -n "Database(" pipez/server.py` shows no module-level call; `grep -n "def recover\|def sweep" pipez/engine.py`.
- `grep -n "setsid\|\.pid" pipez/engine.py` shows the killable runner start and the kill path.
- `cd /tmp && PYTHONPATH=/home/etl/projects/pipez python3 -c "import pipez.server"` leaves no
  `pipez.sqlite3*` in /tmp.
- `.venv/bin/python -m pytest -q` passes (currently 126; expect ≥133). Suite ~4 min — Bash
  timeout ≥ 540000 ms; never conclude it hangs before 8 min.
- No commits, no `pkill` by you, nothing on ports 8400/8401, no new deps, no live PVE calls
  from tests (conftest forces mock sandbox — keep it that way; use a fake client for sweep).

## Write summary
`.odin/sessions/task-15-resilience-summary.md` — mandatory last step.
