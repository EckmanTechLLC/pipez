# Task 33 — Bench: plan-gate metrics and gate-driven revision rounds

## Objective

`scripts/bench_local.py` replays the plan stage of stored runs through chosen tiers and reports
shape metrics (task count, deploy match, forbidden scaffold). Task 32 added a deterministic
plan gate (`pipez/plangate.py`) and a gate-driven revision loop in the engine. Make the bench
measure both: how many gate errors/warnings each tier's first plan has, and — optionally —
how many gate-note revision rounds a tier needs before its plan passes (or whether it ever
does). Also score the stored reference plan so each tier is compared against the plan that
actually shipped.

## Reference (read first)

- `scripts/bench_local.py`: `build_prompt` (~139), `agreement_plan` (~186–205), the per-call
  record built around ~230–275 (`rec.update(ok=…, schema_valid=…, tasks=…, structured=…)`),
  `select_runs`, the table rendering (~280–310: columns and `_mean`/`_fmt`), `--save-outputs`
  handling, `parse_tier`/`tier_dict`.
- `pipez/plangate.py`: `check_plan(plan) -> list[violation]`, `format_note(violations)`,
  violation fields `code, severity, subject, message, hint`.
- `pipez/engine.py` `_plan` (~600–690): how the engine assembles a gate revision — the helper
  that renders `# Previous plan` + the gate note (added by task 32; find it by the
  `gate_note=` keyword) — reuse that helper so the bench sends exactly what the engine
  would. `_validate_plan(run_id, plan)` for cleaning each round's output (pass a dummy run
  id; it only logs).
- `.odin/sessions/task-32-plan-gate-summary.md` for the gate's per-fixture results.
- `tests/test_local_bench.py` for how bench functions are unit-tested (no network).

## Deliverables

1. **Gate metrics on every plan result**: after a plan-stage call succeeds, run
   `plangate.check_plan` on the cleaned output and record `gate_errors` (int),
   `gate_warnings` (int), `gate_codes` (sorted list of distinct error codes). Also compute the
   same for the stored reference plan once per run and record it on each result as
   `ref_gate_errors` / `ref_gate_warnings`. `--save-outputs` JSON keeps the full violation
   lists under `gate_violations`.
2. **`--gate-rounds N`** (default 0): when N > 0 and the first plan has gate errors, send up to
   N revision calls on the *same tier* with the engine's gate-note revision prompt (previous
   plan JSON + `format_note`), re-cleaning and re-checking after each; stop early when errors
   reach 0. Record `gate_rounds_used` (int), `gate_passed` (bool), per-round
   `gate_round_errors` (list of ints, round 0 first), and accumulate `seconds`,
   `input_tokens`, `output_tokens`, `cost` across rounds. A round that fails (timeout,
   schema error, truncation) ends the loop with `gate_passed=False` and the error text in
   `gate_round_error`. The spend estimate printed before running multiplies plan-stage
   estimates by `1 + N` for tiers that are not free.
3. **Table**: add columns `gate-err` (mean errors of the final plan), `ref-err` (mean errors
   of the stored plan), and when `--gate-rounds` > 0: `pass%` and `rounds` (mean rounds used
   among passing results, `-` when none passed). Keep existing columns.
4. **Tests** (`tests/test_local_bench.py`, extend; stub tiers only, no network): a result with
   a plan that has one `dep_coverage` error records `gate_errors == 1` and the code; with
   `--gate-rounds 2` and a stub planner that returns a failing plan then a passing one,
   `gate_rounds_used == 1`, `gate_passed is True`, `gate_round_errors == [n, 0]`, and the
   second call's prompt contains `AUTOMATED PLAN GATE`; a stub that keeps failing stops after
   2 rounds with `gate_passed is False`; `--gate-rounds 0` makes exactly one call.
5. **Docs**: README bench section: the new columns and flag, one sentence on what a gate error
   means.

## Out of scope

- No changes to `pipez/plangate.py`, the engine, prompts or config. No running of the bench
  (the orchestrator runs it). No deploy, no commit, no `.env` edits. Never `pkill`; never
  touch ports 8400/8401, any VM or the Proxmox host; tests must not reach the network.

## Verification criteria

- `.venv/bin/python scripts/bench_local.py --help` shows `--gate-rounds`.
- `.venv/bin/pytest -q tests/test_local_bench.py tests/test_plan_gate.py` passes.
- Full `.venv/bin/pytest -q` passes (known flaky `test_stop_kills_remote_runner_group` may be
  rerun alone).
- A `--dry-run` with `--stages plan --tiers local --gate-rounds 2` prints a spend line (local
  tiers are free; it must not crash on the multiplier).

## Write summary

Last step, mandatory: `.odin/sessions/task-33-bench-plan-gate-summary.md` — files changed,
new flag/columns, test counts before/after, anything undone or uncertain.
