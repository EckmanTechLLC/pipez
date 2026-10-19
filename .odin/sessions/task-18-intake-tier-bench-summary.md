# Task 18 summary

## Changed
- pipez/config.py: `valid_tier(name, default)` helper and `INTAKE_TIER` (env `PIPEZ_INTAKE_TIER`, default deep), defined after `local` is added to MODEL_TIERS; invalid names fall back to deep silently.
- pipez/engine.py `_intake`: uses `config.INTAKE_TIER` (local_allowed branch still first); "deep" literal gone.
- .env.example, README.md: one line each for PIPEZ_INTAKE_TIER. Neither file mentioned PIPEZ_VERIFY_TIER, so the line sits at the end of .env.example and before PIPEZ_MAX_TOKENS in the README table.
- scripts/bench_local.py: `--request-files`, `--project-id`, `--reference-tier`; `synthetic_runs`, `tiers_for`; `agreement_intake(out, run, reference=None)` with `locked_agree`; `one_call` carries reference/request_file/reference_tier; `table()` has locked-J column; JSON has `reference_tier`; estimate and plan lines include synthetic jobs.
- .odin/bench/requests/{cli-tool,node-api,static-site,go-service}.md.
- tests/test_local_bench.py: 4 new tests (tier default/validation, `_intake` tier, request-files dry run, reference agreement).

## Decisions
- The reference tier is always called first for intake jobs and is added to the job's tier list if not in --tiers (planned/estimated accordingly).
- Synthetic runs without --reference-tier get no agreement fields (nothing stored to compare).
- Reference record: `agreement: "reference"`; a failed reference gives None agreement fields for the other tiers.
- Reference mode applies to intake only; brief unchanged.

## Verification
- grep INTAKE_TIER: met.
- Dry-run: met (rc 0). No pipez.sqlite3 exists in the repo, so I ran it against a seeded scratch DB with .venv/bin/python (system python lacks httpx2). Output:
  plan: intake run req:cli-tool  prompt 2430 chars  tiers deep,standard  est $0.0936
  plan: intake run req:go-service  prompt 2492 chars  tiers deep,standard  est $0.0937
  plan: intake run req:node-api  prompt 2412 chars  tiers deep,standard  est $0.0936
  plan: intake run req:static-site  prompt 2498 chars  tiers deep,standard  est $0.0937
  calls: 8   estimated spend: $0.3747 (local is free but ~9 tok/s)
- Full suite: 148 passed (4m17s), met.

## Deferred
- Real-model benchmark run (Odin Prime). Default tier unchanged.
