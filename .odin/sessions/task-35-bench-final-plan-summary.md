# Task 35 summary

Changed:
- scripts/bench_local.py: `_plan_gate` sets `final_output` and `round_outputs=[out]` under save-outputs; `_gate_loop` updates `final_output` and appends each successful round's raw output. Failed rounds are not appended. Present whenever the gate runs (engine given, non-reference); `output` unchanged (round 0).
- tests/test_local_bench.py: fail-then-pass test asserts output=failing, final_output=passing, len(round_outputs)==2.
- README.md: bench paragraph extended with one sentence.

Verification:
- tests/test_local_bench.py: 19 passed (met).
- grep shows final_output/round_outputs set only inside `if save:` (met).

Interpretation: round_outputs stores raw model outputs, not cleaned plans. Fields are absent for results where the gate does not run (reference tier, no engine).
