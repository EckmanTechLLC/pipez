# Task 07c-a summary
- infra/runner/pipez_runner.py: added IGNORED, is_ignored (component-aware for dir entries, suffix for .pyc), filter_paths (IGNORED + git check-ignore --stdin -z, best effort). Applied to files_changed and to git status --porcelain paths (rename "a -> b" handled).
- Tests now re-run before git_state (dirty check/files_changed); run_tests prefixes PYTHONDONTWRITEBYTECODE=1.
- infra/runner/mock_runner.py: duplicated constant + helpers (stdlib only); files_changed filtered.
- tests/test_runner_filter.py: .pyc filtered, src/x.py kept, .pipez/result.json filtered.
Verification: pytest tests/ 80 passed; py_compile infra/runner/*.py ok. Not exercised against a live runner.
Note: filter matches "build/" etc. as whole path components, not substrings.
