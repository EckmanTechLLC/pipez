# Task 26 summary

## Changed
- pipez/engine.py: new `Engine._attach_kept(run_id)` (returns existing handle, else adopts a `kept` row of matching kind, alive -> stored + `sandbox_attached`; dead -> row `destroyed`, error "kept sandbox is gone", `sandbox_gone`; exceptions logged, None).
- `recover()`: second loop re-attaches every `kept` row without a handle; appends `{"run_id","attached"}` to `out["sandboxes"]`.
- `destroy_sandbox`: kept rows go through `_attach_kept` (if gone, row is already destroyed and returned).
- `reverify`: now `async`, calls `_attach_kept`; error reworded to "the build sandbox is gone (vm not reachable)".
- `_acquire_sandbox` (the path `resume` takes for planned runs, via `_run_with_sandbox`) now uses `_attach_kept` instead of ad-hoc adopt.
- pipez/server.py: `await engine.reverify(...)`.
- tests/test_reattach.py (new, 4 tests: recover attaches, dead kept -> destroyed, reverify after re-attach, destroy of detached kept sandbox).
- tests/test_review.py, tests/test_deploy_failure.py: `await engine.reverify`; the old "not attached" test became `test_reverify_refuses_gone_sandbox` (removes the workdir, expects "sandbox is gone").

## Decisions
- `reverify` made async (attach needs await); only caller outside tests is the server route.
- `resume()` is sync, so its re-attach happens in `_acquire_sandbox` (spawned by resume) rather than inline in `resume`; a literal `_attach_kept` call is not inside `resume` itself.
- Kind mismatch in `_attach_kept` returns None without touching the row.

## Verification
- grep shows `_attach_kept`/`sandbox_attached`/`sandbox_gone` in engine.py with callers in recover, reverify, destroy_sandbox, _acquire_sandbox (resume path): met (resume via _acquire_sandbox).
- Full suite: 184 passed (5:03). New file has 4 tests: met.

## Left alone
- Deploy VMs, UI, runner. Task 25 working-tree changes untouched.
