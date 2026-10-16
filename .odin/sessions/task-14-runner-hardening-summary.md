# Task 14 summary - runner hardening

## Changed
- pipez/engine.py: per-leaf settings written to `<wt>/.pipez/runner.json` via `sb.put`; `--test-command`/`--review` removed from the argv in `_execute_code_task`. New `_diagnose_lost_runner` + `_bash_commands` (+ `BASH_REPR`, `import ast`): on no-result and rc != 124, reads the last 3 KB of `logs/<task>.jsonl`, extracts Bash commands, error becomes "runner connection lost (rc=N); last commands: ..." (+ " - a broad kill terminated the runner" for pkill/killall), else the old "runner produced no result (rc=N)". New `_merge_leaf`: the merge moved into the retry loop; on conflict it aborts, logs `merge_conflict`, and does one extra conflict retry (separate counter, does not use `retries_left`) in a fresh worktree off current `integration` with the required failure note; a second conflict raises `merge_conflict: ...` as before.
- infra/runner/pipez_runner.py: `read_settings`, runner.json read (CLI flags still override); `SYSTEM_APPEND` pkill/killall rule; `run_tests` rc 5 with no summary -> passed 0, failed 0, note "no tests collected".
- infra/runner/mock_runner.py: reads runner.json; new `PIPEZ_MOCK_RUNNER_CONFLICT=once` mode (conflict only on first attempt).
- infra/runner/README.md: documents runner.json and rc 5.
- tests: new tests/test_runner_hardening.py (argv clean + runner.json, rc 255 + pkill diagnosis and retry prompt, `_bash_commands`, rc 5, SYSTEM_APPEND, conflict retry succeeds on a fresh worktree); tests/test_code_leaf.py: argv assertion updated, `test_merge_conflict` now injects a racing sibling commit before each merge so the retry conflicts again and the leaf fails.

## Decisions
- Old `test_merge_conflict` could not stay as-is: with the retry, the re-applied leaf merges cleanly (the retry sits on top of the sibling's file). Reworked to force a second conflict.
- Log blocks are stored as repr strings (jsonable + default=str), so `_bash_commands` handles both dict blocks and repr strings, plus a raw scan for a cut-off first line.
- Engine needed no change for rc 5: `failed == 0` already passes.
- "Once per attempt budget" read as exactly one extra conflict retry per leaf execution.

## Verification
- grep runner.json: write in engine.py, reads in both runners - met.
- grep test-command in engine.py: no matches (not on argv) - met.
- grep pkill: prompt rule in pipez_runner.py, diagnosis in engine.py - met.
- Full suite: 126 passed (3:46) - met (>=126).
- No commits, no pkill, no ports 8400/8401, no new deps - met.

## Left alone
Review stage logic, planner, prompts other than SYSTEM_APPEND, UI, deploy, live VM.
