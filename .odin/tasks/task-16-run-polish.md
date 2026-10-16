# Task 16 — Run polish: spend ledger + cost roll-up, header labels, deploy link

## Objective
Operators cannot see what a run costs: leaf costs sit on task rows (SDK-reported), while
intake/brief/plan/decide/synthesis calls only stash token counts in `intake_meta`/`plan_meta`
and are never priced. Add a per-run spend ledger priced from a tier price table, roll it up
to the run header with a per-stage breakdown, and fix two header nits: the resume button on
a `done` run reads "heal gaps" (should read "resume"), and the deploy badge should show the
URL as a clickable link with its status.

## Reference
- `pipez/config.py`: `MODEL_TIERS`, `LOCAL_*`. Current list prices ($/MTok in, out):
  claude-opus-5-5 4 / 20; claude-sonnet-5-5 2 / 10; claude-haiku-5-5 0.10 / 0.50; local 0 / 0.
- `pipez/engine.py`: `_run_llm_call` (run-level calls: intake ~247, brief ~378, plan ~466 —
  they get `(out, usage), tier_name`), `_llm_call` (task-level: decide/synthesize/verify),
  every `self.db.add_tokens(...)` / `add_cost(...)` site (~1541, 1581, 1608, 1653, 1677,
  2099–2101, 2225), `_execute_code_task` (runner JSON has `cost_usd`, `input_tokens`,
  `output_tokens`), `_review`, `_review_with_fixes`, `_integrate_with_fixes`, `_publish_run`.
- `pipez/providers.py`: `usage` dicts `{"input_tokens","output_tokens","model"}` (~113, ~153);
  `pipez/llm.py` MockLLM usage.
- `pipez/db.py`: `MIGRATIONS` (append-only), `add_tokens`, `add_cost`, `get_run`, `get_runs`.
- `pipez/server.py`: `/api/runs`, `/api/runs/{id}`, SSE snapshot (~353).
- `ui/index.html`: run header (~386–397), `codeStats` (~429), resume/heal-gaps (~495–503),
  `deployBadge` (~535), `sandboxBadge`, SSE handlers (~290–325).
- Tests: `tests/test_intake.py`, `tests/test_plan.py`, `tests/test_code_leaf.py`,
  `tests/test_review.py`, `tests/conftest.py`.

## Deliverables
1. **Price table + estimator.** `config.PRICES_PER_MTOK` keyed by model id (env-overridable
   as JSON via `PIPEZ_PRICES_JSON`, merged over the defaults; unknown model → 0 and a one-time
   warning), `config.estimate_cost(model, input_tokens, output_tokens) -> float`.
2. **Spend ledger.** Table `spend(id, run_id, task_id NULL, stage, model, input_tokens,
   output_tokens, cost_usd, source, created_at)` with `source ∈ {estimate, reported}`.
   `db.add_spend(...)` also increments a new `runs.cost_usd` column (migration). Record:
   intake/brief/plan (stage names `intake`,`brief`,`plan`; estimate), decide/synthesize/verify
   via `_llm_call` (stage = the call kind; estimate), code leaves (stage `leaf`, `source`
   reported, the SDK's `cost_usd`), reviewer (`review`), fixers (`fix`). Keep the existing
   `add_tokens`/`add_cost` behaviour on tasks unchanged.
3. **API.** `GET /api/runs/{id}/spend` → `{"total_usd", "by_stage": {stage: {"usd","calls",
   "input_tokens","output_tokens"}}, "by_model": {...}}`. `runs.cost_usd` is in every run
   JSON (`/api/runs`, `/api/runs/{id}`, SSE `run` events). Project roll-up:
   `GET /api/projects/{project_id}/spend` → same shape summed over the project's runs.
4. **UI.** Run header shows `· $X.XX` (two decimals, omit when 0) with a `title` tooltip
   listing stages; clicking it toggles a small spend table (stage / calls / tokens / $) under
   the header. Resume button always reads "resume" (drop "heal gaps"). Deploy badge: when a
   deployment exists show `deploy: <status>` and, when `ready`, the URL as an `<a target=_blank>`;
   keep the undeploy/deploy buttons. Leave the rest of the UI alone.
5. **Tests**: (a) `estimate_cost` for the three models and an unknown one; (b) a mock
   intake→brief→plan run produces spend rows for the three stages and `runs.cost_usd` equals
   their sum (MockLLM must report nonzero tokens for this — add a small fixed usage to
   MockLLM responses, e.g. 1000/100, and fix any test that asserted zeros); (c) a code leaf's
   reported cost appears as a `leaf` row with `source=reported`; (d) `/api/runs/{id}/spend`
   shape via the FastAPI test client (there is a precedent in `tests/test_plan.py::test_api_plan`).

## Out of scope
- Historical back-fill of existing runs; budgets/limits; any engine behaviour change beyond
  recording spend; planner/intake prompts; PVE; committing; the live VM (Odin Prime deploys).

## Verification criteria
- `grep -n "add_spend" pipez/engine.py` shows ≥6 call sites (intake, brief, plan, _llm_call,
  leaf, review/fix path); `grep -n "heal gaps" ui/index.html` returns nothing.
- `.venv/bin/python -m pytest -q` passes (currently 135; expect ≥140). Suite ~4.5 min —
  Bash timeout ≥ 560000 ms; never conclude it hangs before 8 min.
- No commits, no `pkill`, nothing on ports 8400/8401, no new deps.

## Write summary
`.odin/sessions/task-16-run-polish-summary.md` — mandatory last step.
