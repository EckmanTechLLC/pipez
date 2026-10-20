# Task 26 — Re-attach kept build sandboxes after a server restart

## Objective
`Engine.recover()` adopts only `provisioning`/`ready` sandbox rows (marking live ones
`kept`); rows already `kept` from before the restart are skipped, so their in-memory
handle is gone and `reverify` refuses with "kept but not attached (server restarted)"
(live case: run aabf050e8aba, sandbox 9100, after the task-25 deploy restart). Add a
re-attach helper used by `recover()` for every `kept` row and on demand by
`reverify`/`resume`/sandbox destroy, so a kept sandbox survives restarts.

## Reference (read first)
- `pipez/engine.py` `recover()` (~940–995: `_sandbox_factory`, `sb.adopt(row)`,
  `sb.alive()`, `kill_runner`, `_sandbox_row`), `_release_sandbox` (~1277),
  `reverify` (~1540–1560, the "not attached" ValueError), `resume` (grep `def
  resume`), `destroy_sandbox`/`POST /api/sandboxes/{run_id}/destroy` path,
  `self._sandboxes`, `_alive(rid)`.
- `pipez/sandbox.py` `PveSandbox.adopt`, `alive`, `MockSandbox` equivalents, `kind`.
- `tests/test_resilience.py` — how `recover()` is tested with mock sandboxes (rows
  seeded in the DB, a fresh `Engine` built over the same DB).
- `tests/test_deploy_failure.py` — the reverify test and the "not attached" test.

## Deliverables
1. `Engine._attach_kept(run_id) -> Sandbox | None`: if `self._sandboxes` already has
   a handle return it; else read the sandbox row; if status is `kept` and the row's
   kind matches `_sandbox_factory(run_id).kind`, `adopt(row)` and check `alive()`;
   alive → store in `self._sandboxes[run_id]`, log `sandbox_attached {"vmid","ip"}`
   and return it; dead → `_sandbox_row(status="destroyed", destroyed_at, kept=0,
   error="kept sandbox is gone")`, log `sandbox_gone`, return None. Exceptions are
   logged and return None.
2. `recover()`: after the existing sandbox loop, call `_attach_kept` for every row
   with status `kept` and no live handle; include `{"run_id", "attached": bool}` in
   `out["sandboxes"]`.
3. `reverify`, `resume` (when the row is `kept`) and the sandbox destroy path call
   `_attach_kept` before deciding; the "not attached (server restarted)" error remains
   only for the case where re-attaching failed (sandbox dead) — reword to "the build
   sandbox is gone (vm not reachable)".
4. Tests (`tests/test_reattach.py`): (a) seed a `kept` mock sandbox row, build a new
   Engine over the DB, `recover()` → handle present, `sandbox_attached` logged; (b) a
   `kept` row whose mock reports not alive → row `destroyed`, `sandbox_gone` logged;
   (c) `reverify` on a done run with a `kept` row and no handle succeeds after
   re-attach (review runs with the mock reviewer); (d) destroy of a kept-but-detached
   sandbox works. Full suite `python -m pytest -q` (use `.venv/bin/python`; ~5–7 min;
   timeout ≥ 560 000 ms or nohup + poll; do not assume a hang before 8 minutes; rerun
   flaky failures).

## Out of scope
- Re-attaching deploy VMs (deployments already persist vmid/ip/url and need no
  handle); UI; runner.
- Never run `pkill`/`killall`; never touch ports 8400/8401; never touch Proxmox.

## Verification criteria
- `grep -n "_attach_kept\|sandbox_attached\|sandbox_gone" pipez/engine.py` non-empty,
  with calls from `recover`, `reverify`, `resume` and the destroy path.
- `python -m pytest -q` green; new test file has ≥ 4 tests.

## Write summary
`.odin/sessions/task-26-reattach-kept-sandbox-summary.md`: files changed, test
count, anything deferred.
