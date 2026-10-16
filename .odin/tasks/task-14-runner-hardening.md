# Task 14 — Runner hardening: self-kill via pkill, pytest exit 5, merge-conflict retry

## Objective
Live run 60cd5166f16a (pastebin) lost three leaves to engine defects:
(1) the Opus app leaf ran `pkill -f pytest` to clear hung tests; the runner's own argv
contains `--test-command '.venv/bin/python -m pytest -q'`, so the agent killed the runner and
its SSH session → engine saw "runner produced no result (rc=255)" with empty stderr, twice.
(2) a leaf with no tests yet got pytest exit code 5 ("no tests collected") → counted as
"1 failed". (3) an independent leaf conflicted with a sibling on `.gitignore`,
`pyproject.toml`, `pipez.env.example` → engine aborted the merge and failed the leaf instead
of re-applying it on top of `integration`.

## Reference
- `infra/runner/pipez_runner.py`: argparse/`main` (~265–300), `SYSTEM_APPEND`, `run_tests`,
  `parse_summary`, `review_main`; `infra/runner/mock_runner.py`; `infra/runner/README.md`.
- `pipez/engine.py`: `_execute_code_task` (~1760–1910): runner cmd build (`--test-command`),
  `out is None` error (~1840), leaf retry loop (`retries_left`, `failure` tail), merge block
  (`merge --no-ff`, `merge_conflict` log, `merge --abort`, ~1890–1900), worktree re-creation
  on retry (~1800–1815). `_task_md` "Previous attempt failed" section.
- `pipez/sandbox.py`: `run()`, `compose_command`, `put/get`; `MockSandbox`.
- `pipez/config.py`: `LEAF_RETRIES`, `LEAF_TIMEOUT`.
- Tests: `tests/test_code_leaf.py`, `tests/test_runner_filter.py`, `tests/test_control.py`
  (merge conflict test exists: `test_merge_conflict`), `tests/conftest.py`.

## Deliverables
1. **Nothing identifiable on the runner's argv.** Engine writes the per-leaf runner settings to
   `<wt>/.pipez/runner.json` (`{"test_command": ..., "review": bool}`) via `sb.put`, and
   invokes `runner.py --cwd <wt> --task-file .pipez/task.md --model … --max-turns … --max-budget-usd …
   --log …` only. Runner reads `.pipez/runner.json` (keep `--test-command`/`--review` as
   overrides for manual use). `SYSTEM_APPEND` gains: "Never use `pkill`/`killall` with a
   pattern — kill by PID (`$!`, `pgrep -f <unique marker> `) or run tests with `timeout`. Broad
   kills terminate your own session." Mirror the json read in `mock_runner.py`.
2. **Diagnose a lost runner.** When the runner produces no result and rc ∉ {124}: the engine
   fetches the tail of the runner log (`<wd>/logs/<task_id>.jsonl`, last ~3 KB via `sb.get`,
   errors ignored) and extracts the last Bash tool commands; the error becomes
   `runner connection lost (rc=255); last commands: <…>` (or `runner produced no result
   (rc=N)` when nothing is found); if any of those commands contains `pkill`/`killall`, append
   "— a broad kill terminated the runner". This text goes into the retry's "Previous attempt
   failed" section so the agent sees why.
3. **pytest exit 5 is "no tests".** `run_tests`: rc 5 with no summary line → `passed 0,
   failed 0, rc 5, note "no tests collected"`. Engine: a `tests` dict with `failed == 0` passes
   as today (so `LEAF_REQUIRE_TESTS` only rejects `tests: null`).
4. **Merge-conflict retry.** On `merge_conflict`, instead of raising: log it, abort the merge,
   and (once per attempt budget — reuse `retries_left`, plus one extra "conflict" retry that
   does not consume a test-failure retry) re-run the leaf in a fresh worktree off the CURRENT
   `integration` with a failure note: "Your previous attempt conflicted with work merged by a
   sibling task on: <files>. The worktree now contains the merged versions — re-apply your
   changes on top of them, do not overwrite unrelated content." If it conflicts again → fail
   as today.
5. **Tests**: (a) runner.json is written and the runner argv has no `pytest`/`--test-command`
   (mock sandbox records the command); (b) rc 255 + log tail containing a `pkill -f pytest`
   Bash call → error text names the broad kill, and the retry prompt contains it; (c) rc 5
   → tests dict with failed 0, leaf passes; (d) merge conflict → one conflict retry on a
   fresh worktree, then success merges; second conflict → fails as before.

## Out of scope
- Review stage, planner, intake, prompts other than `SYSTEM_APPEND` and the failure note,
  PVE, templates, UI, deploy. Resuming the live run (Odin Prime does that). Committing.

## Verification criteria
- `grep -n "runner.json" pipez/engine.py infra/runner/pipez_runner.py infra/runner/mock_runner.py`
  show write and read; `grep -n "test-command" pipez/engine.py` shows it is NO LONGER on the
  argv built in `_execute_code_task`.
- `grep -n "pkill" infra/runner/pipez_runner.py pipez/engine.py` show the prompt rule and the
  diagnosis.
- `.venv/bin/python -m pytest -q` passes (currently 120; expect ≥126). Suite takes ~4 min —
  Bash timeout ≥ 480000 ms; never conclude it hangs before 7 min.
- No commits, no `pkill` by you either, nothing on ports 8400/8401, no new deps.

## Write summary
`.odin/sessions/task-14-runner-hardening-summary.md` — mandatory last step.
