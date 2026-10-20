# Task 24 — Plan-stage benchmark (tier@effort) and a cheaper setting for plan revisions

## Objective
Planning is the largest cost line (~$0.45–0.60 per Opus/high call, ~20k output tokens
of which ~half is reasoning; every human rejection repeats the full call). (1) Make the
plan prompt a reusable builder and let `scripts/bench_local.py` replay stored plan
prompts — both first plans and revision rounds — through several `tier@effort`
settings, reporting schema validity, structure metrics and agreement with the stored
plan. (2) Add `PIPEZ_PLAN_REVISION_TIER` / `PIPEZ_PLAN_REVISION_EFFORT` so revision
rounds (a previous plan + a human note) can run on a cheaper setting; defaults keep
today's behaviour (`deep`, `PLAN_EFFORT`). `PIPEZ_PLAN_EFFORT` already exists — do not
change its default here.

## Reference (read first)
- `pipez/engine.py` `_plan` (~549–605): system/prompt assembly, revision branch
  (`# Previous plan` + `REVISION NOTE`), `_run_llm_call(run_id, "deep", …)` with
  `dict(tier, effort=config.PLAN_EFFORT)`, `_validate_plan`; `_existing_repo_section`
  (~525); `_intake_prompt`/`_brief_prompt` (~249/379) as the builder pattern;
  `answer_plan` (~696–765) for how `plan_meta.revisions` is recorded.
- `pipez/config.py` `PLAN_EFFORT` (~153), `MODEL_TIERS`, `valid_tier`, `INTAKE_TIER`,
  `offered_tiers`.
- `pipez/llm.py` `plan()`, `plan_schema()`; `pipez/providers.py` effort plumbing (~100).
- `scripts/bench_local.py` — whole file: `DEFAULT_OUT_TOKENS`, `select_runs`,
  `build_prompt`, `one_call`, `agreement_*`, `table`, `main`, `--reference-tier`.
- `tests/test_local_bench.py` — `_stored_run`, `test_prompt_builders_match_old_inline`,
  dry-run tests.

## Deliverables
1. `Engine._plan_prompt(run) -> (system, prompt)` extracted from `_plan` with identical
   text (including HARD LIMITS, Allowed tiers, existing-repo section and the revision
   branch when `run["plan"]` and `plan_meta.revisions` exist); `_plan` uses it. A
   static `Engine._is_revision(run) -> bool`.
2. Config: `PLAN_REVISION_TIER = valid_tier(env PIPEZ_PLAN_REVISION_TIER, default
   "deep")`, `PLAN_REVISION_EFFORT = env PIPEZ_PLAN_REVISION_EFFORT or PLAN_EFFORT`.
   `_plan` uses them when `_is_revision(run)`; log `plan_call {"revision": bool,
   "tier": …, "effort": …}` before the call. Document both in `.env.example` and
   `README.md` next to `PIPEZ_PLAN_EFFORT`.
3. Bench: stages `plan` (stored runs with a plan; the prompt is rebuilt WITHOUT the
   revision branch — pass a copy of the run with `plan=None`) and `plan_revision`
   (stored runs whose `plan_meta.revisions` is non-empty; prompt rebuilt with the
   stored plan + last note). `--tiers` accepts `name[@effort]` (e.g.
   `deep@high,deep@medium,standard`); the tier dict passed to `llm.plan` gets that
   effort. `DEFAULT_OUT_TOKENS` plan 20000 / plan_revision 15000 for estimates.
   Metrics per call (`agreement_plan(out, run)`): `tasks` (count), `modules`,
   `deploy_target`, `deploy_target_match` (== stored plan's), `task_count_delta` (new −
   stored), `scaffold_forbidden` (any scaffold cmd matching `git (init|checkout|clone)`
   or `npm init` when the run has a parent), `has_test_command`, `plan_chars`. Table
   columns for plan stages: n, ok%, p50 s, mean $, mean out-tokens, tasks, Δtasks,
   deploy-match%, forbidden%. `--reference-tier` applies to plan stages too (compare
   against the reference output instead of the stored plan). JSON records carry the
   raw `out` for plan stages under `output` (so a human can diff plans afterwards) —
   also add `--save-outputs` which stores raw outputs for every stage.
4. Tests: `_plan_prompt` equals the old inline text for a non-revision run and a
   revision run (fixture with `plan` + `plan_meta.revisions`); revision uses
   `PLAN_REVISION_TIER`/`EFFORT` (monkeypatch to `standard`/`low`, capture the tier
   passed to `llm.plan`), first plan unchanged; bench `--tiers deep@medium,standard
   --stages plan,plan_revision --dry-run` on a seeded DB prints plan lines with the
   effort and the right job counts; `agreement_plan` pure-function fixture. Full
   suite `python -m pytest -q` (use `.venv/bin/python`; ~5–7 min; timeout ≥ 560 000
   ms or nohup + poll; do not assume a hang before 8 minutes; rerun flaky failures).

## Out of scope
- Changing `PIPEZ_PLAN_EFFORT`'s default or any tier default; running the bench
  against real models (Odin Prime does); planner prompt content changes; UI.
- Never run `pkill`/`killall`; never touch ports 8400/8401; never touch Proxmox.

## Verification criteria
- `grep -n "_plan_prompt\|PLAN_REVISION_TIER\|PLAN_REVISION_EFFORT" pipez/engine.py pipez/config.py` non-empty.
- `.venv/bin/python scripts/bench_local.py --db <seeded tmp db> --stages plan,plan_revision --tiers deep@high,deep@medium,standard --dry-run` exits 0 and shows `@` efforts.
- `python -m pytest -q` green.

## Write summary
`.odin/sessions/task-24-plan-bench-revision-tier-summary.md`: files changed, test
count, the exact bench command to run on the VM DB copy, anything deferred.
