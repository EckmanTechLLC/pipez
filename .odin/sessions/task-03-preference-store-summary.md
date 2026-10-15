# Task 03 summary

## Changed
- pipez/db.py: migrations (runs.project_id, observations + index, preferences), `_backfill_project_ids`, `create_run` inherits/sets project_id.
- pipez/config.py: PREF_WINDOW, PREF_GRADUATE.
- pipez/prefs.py (new): PreferenceStore.
- pipez/server.py: `prefs` store next to `db`; observation/preference routes; GET /api/runs/{run_id}.
- tests/test_prefs.py (new, 11 tests incl. a TestClient API smoke).
- README.md (Preferences section), .odin/project-index.md.

## Decisions / interpretations
- Request-scope observations are recorded but excluded from project resolution and graduation (they are "this run only").
- Graduation requires a strict plurality: a tie for the top count does not graduate.
- Override-with-reason observations still count for project-level resolution (step 3); only graduation excludes them.
- `evidence.count` is total observations for the key when not global; when source is global it is the number of window votes for the winning value.
- pin after retire (or vice versa) replaces the row; pin clears retired_at.
- Invalid scope/source: store raises ValueError, API maps to 400.

## Verification
1. pytest: 18 passed (7 existing + 11 new). Met.
2. Mock server on 8400 with temp DB: 3 observations -> GET preference returned source global/postgres, count 3; root run project_id == its id; child run (parent_run_id) shared project_id. Server stopped, temp dir removed. Met.
3. Old-schema DB (runs table without project_id, 3-run chain) re-opened without error; all three got project_id r1. Met (also covered by test_backfill).
4. grep prefs in server.py shows wiring; grep llm/anthropic in prefs.py: no output. Met.

## Notes
- test_api_smoke imports pipez.server, which opens the default DB path at import (existing module-level behavior), so a ./pipez.sqlite3 file exists in the repo root. Left alone.
- Nothing committed.
