# Task 15 summary - resilience

## Changed
- pipez/server.py: `db`/`prefs` are None at import and built in `startup()` (presets by tests are respected); startup then runs `engine.recover()` and `engine.sweep()` (sweep failure only logged). New route POST /api/sandboxes/sweep (declared before the `{run_id}/destroy` route).
- pipez/engine.py:
  - `recover()`: runs in intake/briefing/planning/provisioning/running/deploying/verifying with no live driver -> active tasks `cancelled`, run `failed`, error "interrupted by server restart", `interrupted` {was} logged on `run:<id>`. Sandboxes `provisioning`/`ready` -> adopt; alive -> remote runner kill + `kept`, else `destroyed`. Deployments `provisioning`/`installing`/`starting` -> VM destroyed, `failed`. Never resumes.
  - `sweep()`: lists the pool, considers only vmids in PVE_VMID_RANGE / DEPLOY_VMID_RANGE, reports `{vmid, name, action}`; destroys only with `config.SWEEP_DESTROY`; kept/ready/provisioning sandbox rows and ready deployment rows whose VM is gone -> `destroyed`. No-op `[]` for mock / PVE-less config unless a `pve_factory` is injected (new `Engine(pve_factory=)` arg, used by tests).
  - Killable runner: `_execute_code_task` wraps the runner as `setsid <cmd> & p=$!; echo $p > <wd>/run/<task>.pid; wait $p; rc=$?; rm -f pid; exit $rc` (rc/stdout preserved; no extra quoting layer, so existing argv-inspecting tests still match). On `CancelledError` and on rc 124 it calls `_kill_runner` (shielded, tracked in `_kills`; `stop()` awaits pending kills). Reviewer and fixer leaves use the same path.
- pipez/sandbox.py: `Sandbox.kill_runner(task_id=None)` (TERM, KILL after 5 s, via a fresh `run`), `Sandbox.killed` list, `MockSandbox.kill_runner` records pgids first. `_spawn` now reaps a killed local child with a 2 s bound (`_reap`): in the mock sandbox the detached runner holds the pipes open, which made cancel block until the runner exited.
- pipez/config.py: `SWEEP_DESTROY` (`PIPEZ_SWEEP_DESTROY`, default 0).
- tests/test_resilience.py (9 tests): import creates no file; recover running run then resume completes (sandbox kept, tasks cancelled); recover planning run resumes through `_plan`; waiting/terminal runs untouched; recover deploying run tears down mock VM; stop kills the runner pid group (asserts `killed` and the real process group gone); sweep reports/destroys with fake PVE client (9000/9001/9090 never touched; kept row with vanished VM -> destroyed); sweep no-op under mock.
- Deleted stray repo-root pipez.sqlite3* (untracked, gitignored).

## Decisions
- Sweep treats sandbox rows `failed`/`destroying` and deployment rows `failed` as tracked (kept-for-inspection VMs), in addition to the spec's list; VMs in `_reserved` (mid-provision) and rows of live runs are skipped.
- Sandbox rows whose kind differs from the factory's kind are left alone in `recover()` (cannot be checked).
- A `deploying` run is set `failed` like the others (spec), so resuming a manually-deployed finished run re-runs through the planned-run path.
- Pool member names are not available from `pool_members`, so orphan `name` is None.

## Verification
- grep Database( in server.py: only inside `startup()` - met. `def recover/sweep` present; `setsid`/`.pid` present - met.
- Import check: with system python3 the import fails (httpx2 missing, pre-existing, not a code issue); with `.venv/bin/python` from an empty dir no file is created - met.
- Full suite: 135 passed (126 before + 9) in ~4m15s - met (>=133).
- No commits, no pkill, no ports 8400/8401, no new deps, no live PVE/VM contact - met.

## Left alone
- UI, planner/intake/prompts, deploy recipe, auto-resume.

## Follow-up
- tests/test_resilience.py::test_stop_kills_remote_runner_group: the single `os.killpg(pgid, 0)` check is now a poll (up to 2 s, 50 ms steps) that waits for the child to call setsid(), failing with a clear message otherwise. Test-only change; engine untouched.
- Verified: test_resilience.py 3 runs in a row, 9 passed each; full suite 139 passed in ~3m58s.
