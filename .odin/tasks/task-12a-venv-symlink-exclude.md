# Task 12a — `.venv` symlink must not make a worktree dirty

## Objective
Task 12 symlinks `<repo>/.venv` into each leaf worktree. Git's exclude pattern `.venv/`
(trailing slash) matches directories only, so the symlink shows as `?? .venv` and the runner
fails the leaf with "uncommitted changes left in the worktree" (seen live: run c6973d74ac0e,
task e3fa7aef — all work committed, 155 tests passing, only the symlink untracked).
Make symlinked `.venv` / `node_modules` ignored everywhere, including on a reused workspace
so the live run is fixed by a plain resume.

## Reference
- `pipez/engine.py`: `BASELINE_GITIGNORE` (~54); `_init_workspace` (~1339–1395): the
  `printf ... >> .git/info/exclude` step (~1366) and the `workspace_reused` early return
  (~1358–1361); the symlink loop in `_execute_code_task` (~1541).
- `infra/runner/pipez_runner.py`: `IGNORED`, `is_ignored`, `filter_paths`, `git_state` (~94).
- `infra/runner/mock_runner.py` (mirror `IGNORED` if it has one).
- `tests/test_runner_filter.py`, `tests/test_control.py::test_baseline_gitignore`,
  `tests/test_code_leaf.py::test_worktree_gets_venv_symlink`.

## Deliverables
1. `.git/info/exclude` lines become `.venv`, `node_modules` (no trailing slash; these match
   both a directory and a symlink). Keep `.pipez/`, `__pycache__/`, `.pytest_cache/`.
   `BASELINE_GITIGNORE`: same change for `.venv` and `node_modules`.
2. On the `workspace_reused` path, before returning, idempotently ensure those two bare
   lines are present in `<repo>/.git/info/exclude` (append only if missing).
3. Runner `is_ignored`: a path whose last component equals `.venv` or `node_modules`
   (e.g. `?? .venv`, `.venv`, `node_modules`) is ignored, in addition to the existing
   directory-component rules. Mirror in `mock_runner.py` if it has its own list.
4. Tests: (a) `is_ignored(".venv")` and `is_ignored("node_modules")` are True, `is_ignored("app/.venvx.py")` False;
   (b) the exclude written by `_init_workspace` contains bare `.venv` and `node_modules`;
   (c) a reused workspace (mock sandbox already has `integration`) gets the lines appended once.

## Out of scope
- Anything else in the engine, runner, prompts, UI, deploy, PVE, template.
- Resuming the live run / deploying to the VM (Odin Prime does that).

## Verification criteria
- `grep -n "info/exclude" pipez/engine.py` shows bare `.venv` and `node_modules` and the
  reused-workspace append.
- `.venv/bin/python -m pytest -q` from the project root passes (≥108 tests; suite takes ~2.5 min —
  run it with a generous timeout, never conclude it hangs before 5 min).
- No commits, no `pkill`, nothing on ports 8400/8401.

## Write summary
`.odin/sessions/task-12a-venv-symlink-exclude-summary.md` — mandatory last step.
