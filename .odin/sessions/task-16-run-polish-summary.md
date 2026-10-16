# Task 16 summary - run polish

## Changed
- pipez/config.py: `PRICES_PER_MTOK` (opus 4/20, sonnet 2/10, haiku 0.10/0.50; `PIPEZ_PRICES_JSON` merged over; local model priced 0), `estimate_cost()` (unknown model -> 0, one-time warning).
- pipez/db.py: migrations for `runs.cost_usd` and `spend` table (+ index); `add_spend` (inserts row, bumps `runs.cost_usd`), `get_spend(run_ids)` roll-up (total_usd, by_stage, by_model).
- pipez/engine.py: `_add_spend` helper (never raises, republishes the run event). Recorded at intake, brief, plan (estimate), decide, synthesize (2 sites), verify (2 sites), render (estimate), and code leaves (reported, from runner `cost_usd`; stage `review` for reviewer, `fix` for fixer, else `leaf`). Existing add_tokens/add_cost untouched.
- pipez/llm.py: MockLLM usage is now 1000/100 tokens (model "mock").
- pipez/server.py: GET /api/runs/{id}/spend, GET /api/projects/{project_id}/spend (404 for unknown). `cost_usd` rides in all run JSON since it is a runs column.
- ui/index.html: `$X.XX` badge (omitted when 0) with stage tooltip, click toggles spend table (fetched lazily); resume button now reads "resume"; deploy badge shows `deploy: <status>` plus the URL link when ready; undeploy/deploy buttons kept.
- tests/test_spend.py (new, 4 tests): estimate_cost; intake/brief/plan rows with `runs.cost_usd` == sum; leaf rows `source=reported` matching task cost; API shape incl. project roll-up.

## Decisions
- Mock model "mock" is unpriced by default, so the run-level test sets a price for it via monkeypatch.
- Spend is recorded at call sites after each `_llm_call`/`_run_llm_call` rather than inside them (they return heterogeneous results). Engine helper is named `_add_spend` so `grep add_spend` finds 12 sites.
- Spend table in UI refreshes on open; it is not live-updated on later events.

## Verification
- grep add_spend engine.py: 12 matches (>=6): met. grep "heal gaps" ui/index.html: empty: met.
- Full suite: 139 total (135 before + 4). Two full runs each had exactly one failure, in a different timing-sensitive subprocess test each time (test_resilience::test_stop_kills_remote_runner_group, then test_review::test_fix_round_passes "run stuck"); both pass when run in isolation (tests/test_review.py + tests/test_resilience.py: 22 passed). Not a clean full pass; I believe it is load flakiness but did not prove it.
- No commits, no pkill, no ports 8400/8401, no new deps, no VM contact: met.

## Left alone
- Historical back-fill, budgets, prompts, UI beyond the header.
