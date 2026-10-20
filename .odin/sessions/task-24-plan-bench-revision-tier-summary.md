# Task 24 summary

Files changed
- pipez/engine.py: added static `_is_revision`, `_plan_prompt(run)` (text identical to the old inline assembly); `_plan` uses it, picks `PLAN_REVISION_TIER`/`EFFORT` for revisions (else deep / `PLAN_EFFORT`), logs `plan_call {revision, tier, effort}` on `run:<id>` before the call.
- pipez/config.py: `PLAN_REVISION_TIER` (valid_tier, default deep), `PLAN_REVISION_EFFORT` (env or PLAN_EFFORT).
- .env.example, README.md: documented PIPEZ_PLAN_EFFORT (was undocumented), PIPEZ_PLAN_REVISION_TIER/EFFORT.
- scripts/bench_local.py: stages `plan` (run copy with plan=None) and `plan_revision` (runs with non-empty plan_meta.revisions); `--tiers name[@effort]`; `agreement_plan`; plan table; `--reference-tier` for plan stages; `--save-outputs`; raw `output` always stored for plan stages; DEFAULT_OUT_TOKENS plan 20000 / plan_revision 15000.
- tests/test_local_bench.py: 5 new tests (prompt equality for first plan and revision, revision tier/effort via spy, config defaults, plan dry run, agreement_plan).

Decisions
- Stored `plan` is the plan produced by the last revision, so plan_revision replays it as the "previous plan" with the last note, as the task specified.
- Reference plan call: its own row reports deploy_target_match and task_count_delta as None. If the reference call fails, other tiers fall back to comparing against the stored plan.
- Tier labels in results/table are the full `name@effort` spec.

Verification
- grep for _plan_prompt / PLAN_REVISION_* in engine.py and config.py: met.
- Bench dry run on a seeded temp DB with deep@high,deep@medium,standard: exit 0, `@` efforts shown: met.
- Full suite: 177 passed in 5:18: met.

Test count: 177 total (5 added).

Command for the VM DB copy (spends real money; drop --yes to see the estimate first):
  .venv/bin/python scripts/bench_local.py --db <vm-db-copy> --stages plan,plan_revision --tiers deep@high,deep@medium,standard --save-outputs --yes --out bench
  (add --reference-tier deep@high to score others against it; --max-per-stage N bounds spend, default 2)

Deferred: nothing; no real-model runs made.
