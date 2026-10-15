# Task 12a summary

Changed
- pipez/engine.py: BASELINE_GITIGNORE and the `.git/info/exclude` printf now use bare `.venv` and `node_modules`. On the `workspace_reused` path, a shell loop appends each of the two lines to `<repo>/.git/info/exclude` only if `grep -qxF` finds it missing, then returns.
- infra/runner/pipez_runner.py and infra/runner/mock_runner.py: `is_ignored` returns True when the last path component (after the last "/" and the last space, so "?? .venv" works) is `.venv` or `node_modules`. `IGNORED` is unchanged.
- tests/test_runner_filter.py: new `test_symlink_names_ignored` (.venv, node_modules, "?? .venv" ignored; "app/.venvx.py" not).
- tests/test_code_leaf.py: new `test_exclude_has_bare_venv_and_reused_appends_once`. It checks the written exclude has the bare lines, strips them, re-runs `_init_workspace` twice on the reused workspace, and asserts each line appears exactly once.
- tests/test_control.py::test_baseline_gitignore: expects bare `.venv` and `node_modules`.

Decisions
- The "?? .venv" case needed splitting on the last space, since the status prefix is part of the path string.
- The reused-workspace test re-adopts the kept sandbox into `engine._sandboxes`, because the run pops it when it finishes.

Verification
- `grep -n "info/exclude" pipez/engine.py` shows the bare lines (line 1369) and the reused append (lines 1360-1361): met.
- Full suite: 107 passed in about 125s: partly met. The criterion says at least 108, but I added 2 tests and edited 1, and the baseline count was apparently 105. All tests pass.
- No commits, no `pkill`, nothing on ports 8400/8401: met.

Left alone: everything else in the engine, runner, prompts, UI, deploy.
