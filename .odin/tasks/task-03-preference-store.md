# Task 03: Preference store with precedence

## Objective
Add the memory that lets pipez stop asking questions it already knows the answer
to. A preference store records **observations** (one human decision each) and
resolves a **preference** per key with strict precedence: a value given in the
current request beats the project's prior answer, which beats a learned global
default, which beats nothing (→ the intake must ask). Defaults graduate only
after repeated consistent answers; explicit overrides with a stated reason are
recorded as exceptions and do not erode the default. This task is storage +
resolution + HTTP API + tests only — no LLM calls, no UI, no free-text
extraction (task 04 feeds this store from the intake form).

## Reference (read first)
- `.odin/project-index.md`
- `pipez/db.py` — `Database`, `MIGRATIONS` (append-only; each ALTER/CREATE swallows OperationalError), `_exec`/`_query`, `create_run`, `get_run`
- `pipez/server.py` — route style, Pydantic bodies, how `db` is a module global
- `pipez/engine.py` — `submit()` → `db.create_run(request, context, parent_run_id)`; keep the signature working
- `tests/conftest.py`, `tests/test_escalation.py` — temp-DB and config monkeypatch patterns to reuse

## Design (binding)
- **Project = run lineage.** `runs` gains `project_id TEXT`. `create_run`: if `parent_run_id` is given, inherit its `project_id`; otherwise `project_id = run_id`. Migration backfills existing rows: roots get their own id, children walk `parent_run_id` to the root.
- **Keys** are freeform lowercase dotted strings (`storage.database`, `license`, `hosting.target`, `ui.theme`); **values** are strings. No fixed vocabulary.
- **Scopes:** `request` (this run only — not persisted as a preference, but recorded as an observation), `project`, `global`.
- **Observation sources:** `intake` (answered on the intake form), `request` (stated in the request text), `override` (user changed a pre-filled default), plus free-text `reason`.
- **Resolution** `resolve(key, project_id, request_values: dict) -> dict`:
  1. `request_values[key]` → `{value, source: "request"}`
  2. a `pinned` global preference for the key → `{value, source: "pinned"}`
  3. latest observation for the key within this `project_id` → `{value, source: "project"}`
  4. learned global default → `{value, source: "global", evidence: {...}}`
  5. otherwise `{value: None, source: "none"}` (intake must ask).
  Always include `evidence`: `{count, last_seen, projects: [...], exceptions: [{value, reason, project_id, ts}, ...]}`.
- **Graduation** of a global default: take the most recent `PIPEZ_PREF_WINDOW` (default 5) observations for the key across all projects, **excluding** observations whose `source == "override"` AND `reason` is non-empty (those are exceptions). If at least `PIPEZ_PREF_GRADUATE` (default 3) of them share one value and that value is the plurality, it is the default. A `retired` preference row suppresses the global default for that key until a new observation arrives after `retired_at`.
- **Pinned** preferences (`PUT /api/preferences/{key}`) always win at step 2 regardless of counts; `DELETE /api/preferences/{key}` retires the key (does not delete observations).

## Deliverables
1. **Schema** (append to `MIGRATIONS`, plus the Python backfill for `project_id`):
   - `ALTER TABLE runs ADD COLUMN project_id TEXT`
   - `observations(id INTEGER PK, key TEXT NOT NULL, value TEXT NOT NULL, scope TEXT NOT NULL, project_id TEXT, run_id TEXT, source TEXT NOT NULL, reason TEXT, ts REAL NOT NULL)` with index on `(key, ts)`
   - `preferences(key TEXT PRIMARY KEY, status TEXT NOT NULL, value TEXT, updated_at REAL NOT NULL, retired_at REAL)` where `status ∈ {pinned, retired}` (learned defaults are *computed*, never stored here)
   - `create_run` sets `project_id` as designed; `get_run`/`get_runs` return it.
2. **`pipez/prefs.py`** (new): `class PreferenceStore(db)` with `observe(key, value, scope, source, project_id=None, run_id=None, reason=None) -> dict`, `observations(key=None, project_id=None, limit=100)`, `resolve(key, project_id=None, request_values=None) -> dict`, `resolve_all(project_id=None, request_values=None) -> dict[key, resolved]` (every key that has any observation or preference row), `pin(key, value)`, `retire(key)`, `learned_default(key) -> dict | None` (the graduation rule, exposed for tests). Config: `PREF_WINDOW`, `PREF_GRADUATE` in `config.py` from `PIPEZ_PREF_WINDOW` / `PIPEZ_PREF_GRADUATE`.
3. **HTTP API** in `server.py` (store instantiated next to `db`):
   - `POST /api/observations` body `{key, value, scope, source, project_id?, run_id?, reason?}` → the observation row. Validate scope/source enums → 400.
   - `GET /api/observations?key=&project_id=&limit=`
   - `GET /api/preferences?project_id=` → `resolve_all`
   - `GET /api/preferences/{key}?project_id=` → `resolve`
   - `PUT /api/preferences/{key}` body `{value}` → pin; `DELETE /api/preferences/{key}` → retire
   - `GET /api/runs/{run_id}` (new, single run — needed so a client can read `project_id`).
4. **Tests** `tests/test_prefs.py` (temp DB; no server needed except one API smoke via `fastapi.testclient` if trivially available, otherwise skip API test and say so):
   - no observations → `source: "none"`
   - 2 consistent `intake` observations → still `none`; a 3rd → `global` with `evidence.count == 3`
   - 3× postgres then `override` "mssql" with reason "client mandated" → still `global: postgres`, and `evidence.exceptions` has the mssql entry
   - 3× postgres then 3× sqlite without reasons (window 5) → default flips to sqlite (plurality within the window)
   - project precedence: global postgres, this project's latest observation sqlite → `project: sqlite`; another project → `global: postgres`
   - `request_values` beats everything; `pin` beats project; `retire` suppresses global until a newer observation
   - run lineage: `create_run(A)`, `create_run(B, parent_run_id=A)` → same `project_id`; migration backfill gives pre-existing runs ids (create rows with `project_id NULL` directly, re-open `Database`, assert filled).
5. **Docs**: README gets a "Preferences" section (the precedence list, the graduation rule, the two env vars, the key naming convention with 4 examples); `.odin/project-index.md` gains `prefs.py` and the new routes.

## Out of scope
- Any LLM call; free-text extraction of preferences from requests or answers (task 04).
- UI (task 05). Intake stage (task 04).
- Deleting observations (append-only audit; retire/pin only).
- Changing engine execution, prompts, or tiers.
- Committing to git.

## Verification criteria
1. `.venv/bin/python -m pytest -q tests/` passes (existing 7 + new).
2. Mock-mode server smoke on 8400 with temp DB: POST `/api/observations` ×3 for `storage.database=postgres` (`scope: global, source: intake`) → `GET /api/preferences/storage.database` returns `source: "global"`, `value: "postgres"`. Then POST a `request` and confirm `GET /api/runs/<id>` shows `project_id == id`; POST a second request with `parent_run_id` and confirm it shares the `project_id`. Stop server, delete temp paths.
3. Re-opening a pre-task-03 database (copy `tests` fixture or create one with the old schema) does not error and runs get `project_id` backfilled.
4. `grep -n "prefs" pipez/server.py` shows the store wired; `grep -rn "llm\|anthropic" pipez/prefs.py` → nothing.

## Write summary
MANDATORY, last step: `.odin/sessions/task-03-preference-store-summary.md`: files, trimmed verification output, any ambiguity resolved (what you chose and why).
