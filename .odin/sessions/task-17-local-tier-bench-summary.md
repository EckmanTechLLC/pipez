# Task 17 summary

## Changed
- pipez/config.py: `LOCAL_ALLOWED_STAGES` (`PIPEZ_LOCAL_STAGES`), helpers `local_allowed(stage)`, `offered_tiers(stage)`.
- pipez/llm.py: `plan_schema()` / `decision_schema()` tier enums use `offered_tiers("plan"/"decide")` (local only when stage listed).
- pipez/engine.py: `_intake_prompt(run)`, `_brief_prompt(run, sources)` factored out (identical strings); `_intake`/`_brief_and_start` start on `local` only if their stage is listed (else deep/standard as before); `_plan` system prompt gets an "Allowed tiers: ..." line; `_materialise_plan` coerces code task `local` -> `light` and logs `tier_coerced` (reason "code tasks run on the Agent SDK"); `_leaf_model` maps `local` -> `light`.
- pipez/providers.py: truncation error now reads "response truncated at max_tokens (local: Ns, N output tokens)" (still matches the "truncated" retry check).
- scripts/bench_local.py (new) + scripts/__init__.py (needed for test import): CLI per spec; copies the DB via sqlite backup (source never touched), Engine built outside a loop (no probe), refuses mock, estimate + `--yes`, `--dry-run`, `--max-per-stage` (default 2), writes bench-<ts>.json and prints the markdown table (extra columns: len ratio, headings%).
- .env.example (`PIPEZ_LOCAL_STAGES=`), README "Local tier" section.
- tests/test_local_bench.py (5 tests: coercion+log, schema gating, plan prompt gating, prompt-builder snapshot, bench dry run); tests/test_providers.py: `test_decision_schema_local_enum` updated to list `decide` in the allowed stages (old test assumed local always offered).

## Decisions
- "Prompt shows local only when allowed" implemented via the schema tier enums plus the appended "Allowed tiers" line; the seeded prompt text mentioning 'local' "when allowed" is unchanged (wording out of scope).
- Fixer/reviewer tiers already fixed ("deep", VERIFY_TIER); asserted VERIFY_TIER != local in a test.
- Brief sources are unknown for stored runs, so the bench uses the default "intake" source.
- synthesize is accepted as a stage name but synthesis tiers come from task tiers, so there is no separate auto-selection to gate.

## Verification
- grep for LOCAL_ALLOWED_STAGES / tier_coerced and ls scripts/bench_local.py: met.
- `scripts/bench_local.py --help` exits 0: met.
- Full pytest: first run 143 passed, 1 failed (the providers test above); after the fix only tests/test_providers.py was re-run (passes); full suite not re-run afterwards. Expected total 144.
- No commits, no pkill, no ports 8400/8401, no new deps, no network in tests, benchmark not run for real: met.
