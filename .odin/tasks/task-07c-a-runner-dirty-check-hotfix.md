# Task 07c-a (hotfix): runner false-negative on build artefacts

## Objective
The first live run of `infra/runner/pipez_runner.py` succeeded (commit made,
tests passed, $0.027) but reported `ok: false, error: "uncommitted changes left
in the worktree"` because the runner re-ran the test command after the agent's
commit, which created `tests/__pycache__/*.pyc`; that path also leaked into
`files_changed`. Make the dirty check and the changed-files list ignore build
artefacts, and make the test re-run happen in a way that cannot dirty the tree.

## Reference
- `infra/runner/pipez_runner.py` (dirty check via `git status --porcelain`; `files_changed` via `git diff --name-only integration...HEAD`; test re-run)
- Live evidence: `files_changed` contained `tests/__pycache__/test_hello.cpython-312-pytest-9.1.1.pyc`.

## Deliverables
1. In the runner, a single `IGNORED = ("__pycache__/", ".pyc", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/", "node_modules/", ".pipez/", ".venv/", "dist/", "build/")` filter applied to both the dirty check (`git status --porcelain` lines) and `files_changed`; also exclude anything matched by `git check-ignore` (run once with the candidate paths) so the repo's own `.gitignore` is honoured.
2. Run the test command **before** the dirty check (order: agent finishes → re-run tests → dirty check → files_changed), and set `PYTHONDONTWRITEBYTECODE=1` in the test re-run's environment.
3. `infra/runner/mock_runner.py` applies the same filter (shared helper or duplicated constant, keep both runners stdlib-only).
4. A tiny stdlib unit test `tests/test_runner_filter.py` importing the filter function from `infra/runner/pipez_runner.py` (add `infra/runner` to `sys.path` in the test) covering: `.pyc` filtered, `src/x.py` kept, `.pipez/result.json` filtered.

## Out of scope
Everything else; do not touch `pipez/` (another agent is editing it), port 8400, infra/pve, git.

## Verification criteria
`.venv/bin/python -m pytest -q tests/` passes; `python3 -m py_compile infra/runner/*.py` passes.

## Write summary
`.odin/sessions/task-07c-a-runner-dirty-check-hotfix-summary.md` (short).
