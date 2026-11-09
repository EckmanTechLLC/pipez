# Task 35 — Bench: save the final-round plan, not just the first

## Objective

With `--gate-rounds N`, `scripts/bench_local.py` records gate metrics for the final plan but
`--save-outputs` stores only the round-0 plan under `output`; the final-round plan (the one
whose `gate_errors`/`gate_violations` are reported) is lost. Store it so it can be replayed
as a fixture and compared.

## Reference

- `scripts/bench_local.py`: the gate-round loop (~170–215, `gate_rounds_used`,
  `gate_round_errors`) and the result record assembly (~279–350, `output`, `gate_violations`).
- `tests/test_local_bench.py` gate-round tests (stub planner returning failing then passing).
- `.odin/sessions/task-33-bench-plan-gate-summary.md`.

## Deliverables

1. With `--save-outputs`, each plan result keeps `output` (round 0, unchanged) and adds
   `final_output` (the plan after the last round; equal to `output` when no round ran) and
   `round_outputs` (list, round 0 first). Without `--save-outputs` none of the three are
   stored (as today for `output`).
2. Test: in the existing fail-then-pass stub test assert `final_output` is the passing plan,
   `output` is the failing one, and `len(round_outputs) == 2`.
3. One line in the README bench paragraph.

## Out of scope

Everything else. No `.env`, commit, deploy; never `pkill`; never touch ports 8400/8401, VMs or
the Proxmox host; no network in tests. A bench process is running from an already-imported
copy of the script — editing the file is fine; do not touch anything under /tmp.

## Verification criteria

- `.venv/bin/pytest -q tests/test_local_bench.py` passes.
- `grep -n "final_output\|round_outputs" scripts/bench_local.py` shows both set only under
  save-outputs.

## Write summary

`.odin/sessions/task-35-bench-final-plan-summary.md` — files changed, test counts.
