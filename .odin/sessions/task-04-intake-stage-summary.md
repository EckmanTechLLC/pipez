# Task 04 summary

## Changed
- pipez/db.py: migrations for runs.decisions/answers/brief/intake_meta; `set_intake`, `set_answers`, `set_brief`; `create_run(status=)`; `get_run`/`get_runs` JSON-decode decisions, answers, intake_meta.
- pipez/llm.py: `INTAKE_SCHEMA`, `BRIEF_SCHEMA`, `LLM.intake/brief`, `MockLLM.intake/brief`.
- pipez/engine.py: `Engine(db, bus, llm, prefs=None)`; `submit` -> status intake; `_intake`, `answer_intake`, `_brief_and_start`, `_run_llm_call` (run-level escalation, logs to task_log `run:<id>`), `_decision_table`, `_set_phase`, `_publish_intake`; `resume()` handles awaiting_human (ValueError), intake/briefing/no-tree (re-run intake).
- pipez/server.py: GET/POST /api/runs/{id}/intake; SSE snapshot `intake`; POST /api/requests returns `status: intake`; Engine gets `prefs`.
- pipez/seed_prompts.py: `intake_discovery`, `intake_brief`.
- tests/test_intake.py (6 tests), README "Intake" section, .odin/project-index.md.

## Decisions / interpretations
- INTAKE_SCHEMA wire form of `request_values` is an array of {key,value} (strict structured outputs cannot express an open map with additionalProperties:false); `LLM.intake()` converts to the dict form, so callers/Mock see `{key: value}`.
- Locked decisions are auto-filled with the request value even without accept_all (source `request`).
- Unknown answer keys -> 400. Answer values are not restricted to the listed options (rendered "(custom answer)" in the table).
- Answering when status != awaiting_human -> 400.
- Brief = model markdown + Constraints/Open questions bullets + `## Decisions` table (code-built, columns key|question|chosen|consequences|source). Stored in runs.brief and as root `context`.
- "Binding Project context": `_task_prompt` now injects `runs.brief` under "Project context (binding)" and skips the identical root-chain context (also in `_synthesize`) to avoid duplication. Existing `runs.context` handling unchanged.
- No root task exists during intake; intake token usage lives in `intake_meta` (discovery/brief), not task rows.
- `get_runs` also decodes JSON columns (affects GET /api/runs shape: decisions etc. are objects).

## Verification
1. pytest: 24 passed (18 prior + 6 new). Met.
2. Mock server on 8400, temp DB: POST -> {"status":"intake"}; GET intake -> awaiting_human, 3 decisions; GET /api/runs -> ['awaiting_human']; POST {"accept_all":true} -> briefing, done after ~10 s; brief contains `| key |` table; /api/observations: 3 rows (intake sources), all with run_id; root task context starts with the brief (exact equality asserted in test_accept_all_flow). Server stopped, temp dirs removed. Met.
3. /api/events first snapshot contained `intake` keyed by run id (status awaiting_human; keys status/decisions/answers/brief). Met.
4. grep shows both prompts at seed_prompts.py lines 145 and 165. Met.

## Left alone
- No git commit. Pre-existing ./pipez.sqlite3 in repo root untouched. No live LLM calls.

## Example GET /api/runs/{id}/intake (mock, after accept_all, trimmed to 1 of 3 decisions)
{"status":"done",
 "decisions":[{"key":"storage.database","question":"Which database?",
   "why_it_matters":"Determines persistence and ops burden.",
   "options":[{"value":"postgres","label":"postgres","consequences":"mock: choosing postgres"},
              {"value":"sqlite","label":"sqlite","consequences":"mock: choosing sqlite"},
              {"value":"none","label":"none","consequences":"mock: choosing none"}],
   "proposed_default":"sqlite","required":false,
   "prefill":{"value":"sqlite","source":"model",
              "evidence":{"count":0,"last_seen":null,"projects":[],"exceptions":[]}},
   "locked":false}],
 "answers":{"storage.database":"sqlite","license":"MIT","hosting.target":"container"},
 "brief":"# Build brief (mock)\n\nBuild what was requested, honouring th..."}
While awaiting_human: answers and brief are null. POST body: {"answers":{},"reasons":{},"accept_all":false}; 400 detail e.g. "required decisions unanswered: license".
