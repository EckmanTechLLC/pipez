# Task 33 summary — bench: plan-gate metrics and gate rounds

## Changed
- scripts/bench_local.py: imports plangate; `--gate-rounds N` (default 0); `_plan_gate`, `_gate_loop`,
  `_gate_counts`, `_clean` helpers; `one_call` takes `engine`, `gate_rounds` and runs the gate on every
  non-reference plan result; spend estimate for plan stages multiplied by `1 + N`; `plan_table` gains
  `gate-err`, `ref-err` and (only when any result has rounds data) `pass%`, `rounds`.
- tests/test_local_bench.py: 5 new tests (stub planner, no network).
- README.md: bench section paragraph on columns, flag, meaning of a gate error.

## Result fields
- Per plan result: gate_errors, gate_warnings, gate_codes (sorted distinct error codes),
  ref_gate_errors, ref_gate_warnings; gate_violations only with --save-outputs.
- With --gate-rounds > 0: gate_rounds_used, gate_passed, gate_round_errors (round 0 first), gate_round_error
  on a failed round; seconds/input_tokens/output_tokens/cost accumulate. gate_errors/warnings/codes reflect
  the final plan.

## Decisions
- Revision prompt built with `engine._plan_prompt(run, gate_plan=..., gate_note=format_note(...))`; for the
  `plan` stage the run is `dict(run, plan=None)` as in build_prompt, so exactly the engine's prompt.
- Reference tier (is_reference) results get no gate metrics. If the first plan cannot be cleaned
  (`_validate_plan` raises) the result records `gate_error` and no gate counts (not in spec).
- `ref_gate_*` computed per result (deterministic, cheap) rather than cached per run.
- `pass%` counts ok results with gate data; a failed first call is excluded from it.

## Verification
- `--help` shows --gate-rounds: met.
- test_local_bench + test_plan_gate: 33 passed (was 28 before; +5): met.
- Full suite: 241 passed (was 236): met.
- Dry run with --stages plan --tiers local --gate-rounds 2 (local env vars, temp DB in scratchpad) printed
  the spend line, $0.0000, no crash: met.

## Not done
- Bench not run against real endpoints. Nothing committed; .env untouched; /tmp benchmark untouched.
