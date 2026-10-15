# Task 04: Intake stage — decision discovery, human answers, build brief

## Objective
Put a decision-first intake phase in front of the build tree. On submit, pipez
asks the deep tier to discover every human-necessary decision the request
implies, pre-fills each one from the preference store (request text > project >
learned default), and pauses the run as `awaiting_human`. The human answers (or
accepts all) through the API; every answer becomes an observation; the standard
tier then writes a **build brief** (markdown + decision table) that is stored on
the run and injected as binding context for the normal decompose/complete tree.
Intake always pauses, even when everything is pre-resolved. No UI in this task
(task 05); the API must be complete enough that the UI is purely a client.

## Reference (read first)
- `.odin/project-index.md`; summaries in `.odin/sessions/` for tasks 01–03
- `pipez/engine.py` — `submit()`, `_run()`, `_execute()`, `_llm_call()` (tier/effort/escalation wrapper), `_publish_run/_publish_task`, `resume()`
- `pipez/llm.py` — facade: `decide/synthesize/verify`, `decision_schema()`, `_call(system, prompt, schema, fetch, tier)`; `MockLLM`
- `pipez/prefs.py` — `PreferenceStore.resolve(key, project_id, request_values)`, `resolve_all`, `observe(...)`
- `pipez/db.py` — `runs` columns incl. `project_id`, `MIGRATIONS`, `finish_run`, `set_run_status`
- `pipez/server.py` — routes, `SubmitRequest`, SSE snapshot composition
- `pipez/seed_prompts.py` — prompt-as-data pattern; `_seed:` shadowing

## Design (binding)
- **Run states** (`runs.status`): `intake` → `awaiting_human` → `briefing` → `running` → (existing: `done|failed|verifying|rendering`). `resume()` must treat `awaiting_human` as "nothing to resume" (400) and `briefing`/`intake` as resumable by re-running intake from scratch.
- **Intake record** lives on the run: new columns `decisions TEXT` (JSON), `answers TEXT` (JSON), `brief TEXT` (markdown), `intake_meta TEXT` (JSON: usage, model, timings). No new task rows for intake — it is a run phase, not a tree node.
- **Discovery output** `INTAKE_SCHEMA`:
  ```
  { "summary": str,
    "request_values": { key: value },          # decisions the request text already settles, verbatim
    "decisions": [ { "key": str,               # lowercase dotted, e.g. storage.database
                     "question": str,
                     "why_it_matters": str,
                     "options": [ { "value": str, "label": str, "consequences": str } ],  # 2–6
                     "proposed_default": str,  # one of options[].value
                     "required": bool } ] }    # true = build cannot proceed without a human answer
  ```
  All fields required; `additionalProperties: false` throughout.
- **Pre-fill**: for each decision, `prefs.resolve(key, run.project_id, request_values)`. Attach to the stored decision: `prefill: {value, source: request|pinned|project|global|model, evidence}`. When `source == "none"`, prefill comes from `proposed_default` with `source: "model"`. Decisions whose key is in `request_values` are still listed (so the human sees them) but marked `locked: true`.
- **Answer endpoint** `POST /api/runs/{id}/intake` body `{ "answers": { key: value }, "reasons": { key: str }?, "accept_all": bool? }`. `accept_all` fills every unanswered key with its prefill. Any `required` decision still unanswered → 400 listing the keys. Every answered key → `prefs.observe(key, value, scope="global", source=..., project_id, run_id, reason)` where `source = "request"` if `locked`, `"override"` if the answer differs from a non-model prefill, else `"intake"`. Then status → `briefing`.
- **Brief** `BRIEF_SCHEMA`: `{ "brief": str (markdown), "constraints": [str], "open_questions": [str] }`. Prompt = original request + project context (if any) + the answered decision table (key, question, chosen value, consequences, source). Stored in `runs.brief`; the decision table is appended to the brief as a markdown table by code (not by the model) so it is always exact. Then the existing tree starts with `context = brief` (binding "Project context") — the root task is created at this point, not at submit.
- **Tiers**: discovery uses `MODEL_TIERS["deep"]`, brief uses `MODEL_TIERS["standard"]`, both through `_llm_call`-equivalent escalation/effort handling (write a small run-level wrapper since `_llm_call` is task-keyed; log escalations to a new `run_log` table? — No: log them to `task_log` with `task_id = run_id` prefixed `run:`, keep one table).
- **Mock mode**: `MockLLM.intake()` returns 3 decisions (`storage.database` with options postgres/sqlite/none, `license` MIT/Apache-2.0/proprietary, `hosting.target` vm/container/static) with `request_values` `{}` and `required` true on `license` only; `MockLLM.brief()` returns a short markdown brief echoing the answers.
- **Prompts** (seeded, data): `intake_discovery` (what a human-necessary decision is: licensing, EULA, deployment/hosting, DB/storage, auth, UI framework & theme, language/runtime, repo/CI, data retention, pricing; vs. what is NOT one — anything a competent engineer decides from the stated requirements; keep options concrete with consequences; never invent requirements; put anything the request already states into `request_values`), `intake_brief` (write for a build team that will not get to ask questions; every decision becomes a constraint; flag `open_questions` only for things no option could settle).

## Deliverables
1. **Schema + state**: migrations for `runs.decisions`, `runs.answers`, `runs.brief`, `runs.intake_meta`; `db` helpers `set_intake(run_id, decisions, meta)`, `set_answers(run_id, answers)`, `set_brief(run_id, brief, meta)`; `get_run` returns the new columns (JSON-decoded where applicable).
2. **LLM**: `INTAKE_SCHEMA`, `BRIEF_SCHEMA`, `LLM.intake(system, prompt, tier)`, `LLM.brief(system, prompt, tier)`, `MockLLM` equivalents.
3. **Engine**: `submit()` creates the run in status `intake` and schedules `_intake(run_id)`; `_intake` runs discovery, pre-fills via `PreferenceStore`, stores decisions, sets `awaiting_human`, publishes. `answer_intake(run_id, answers, reasons, accept_all)` validates, observes, sets `briefing`, schedules `_brief_and_start(run_id)` which writes the brief, creates the root task with `context=brief`, sets `running`, and continues into the existing `_run` path. `Engine.__init__` takes the `PreferenceStore`.
4. **API**: `POST /api/runs/{id}/intake` (answers); `GET /api/runs/{id}/intake` → `{status, decisions (with prefill/locked), answers, brief}`; SSE `snapshot` includes `intake` per run and a new event `{"type":"intake","run_id":...,"status":...}` is published on each transition. `POST /api/requests` response gains `"status": "intake"`.
5. **Prompts + docs**: the two seed prompts above; README "Intake" section (states, endpoints, how accept-all and overrides are recorded); `.odin/project-index.md` updated.
6. **Tests** `tests/test_intake.py` (mock LLM, temp DB, `fastapi.testclient` if available — task 03 will have established whether it is):
   - submit → status `awaiting_human`, 3 decisions, `license.prefill.source == "model"`
   - pre-seed 3 global observations `storage.database=postgres` → prefill `source == "global"`, value postgres
   - POST answers missing `license` → 400 naming it
   - POST `accept_all` → observations created with correct sources (`intake` for accepted model default, `override` + reason when the body changes a `global` prefill), run proceeds to `done` (mock tree), `runs.brief` non-empty and contains the markdown decision table, root task `context == brief`
   - `resume()` on `awaiting_human` → ValueError

## Out of scope
- UI (task 05). Free-text preference extraction beyond `request_values` returned by the discovery call.
- Mid-run `awaiting_human` pauses inside the tree (task 08).
- Changing the decompose/complete prompts or the decision schema.
- Any live LLM call during verification.
- Committing to git.

## Verification criteria
1. `.venv/bin/python -m pytest -q tests/` passes (all prior + new).
2. Mock server smoke on 8400 with temp DB: POST request → `GET /api/runs/{id}/intake` shows `awaiting_human` + 3 decisions; `GET /api/runs` shows status `awaiting_human`; POST `/api/runs/{id}/intake` with `{"accept_all": true}` → within ~20 s run is `done`, `GET /api/runs/{id}` has `brief` containing a `| key |` table header, `GET /api/observations` shows 3 rows with `run_id` set, `GET /api/runs/{id}/tasks` root task `context` equals the brief. Stop server, delete temp paths.
3. `GET /api/events` snapshot JSON includes an `intake` object for that run.
4. `grep -n "intake_discovery\|intake_brief" pipez/seed_prompts.py` shows both prompts.

## Write summary
MANDATORY, last step: `.odin/sessions/task-04-intake-stage-summary.md`: files, trimmed verification output, ambiguities resolved, and the exact JSON of one mock `GET /api/runs/{id}/intake` response (trimmed) so the UI task can build against it.
