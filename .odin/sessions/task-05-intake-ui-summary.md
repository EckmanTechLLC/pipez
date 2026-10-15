# Task 05 summary: Intake UI

Changed only `ui/index.html` (plus this file). No backend change; the API was sufficient.

## What changed in the page
- CSS: `.s-intake/.s-briefing/.s-planning` (blue-grey), `.s-awaiting_plan` (purple, reserve), `.s-awaiting_human` (accent + pulsing dot); `#intake` panel has accent border and glow animation; decision card, badge, option, reason box, brief, preferences drawer styles.
- Markup: Preferences button in header, `#prefs` fixed right drawer, `#intake`, `#stage-msg`, `#briefwrap` inside `#left`, table given id `tasktable`.
- JS state: `intakeByRun`, `drafts` (form state survives the 1s render tick), `inflight`, `intakeErr`.
- JS functions: `mergeRunIntake`, `refreshIntake` (GET /intake on the SSE `intake` event), `intakeOf`, `stageHidesTable`, `draftFor`, `answerOf`, `needsReason`, `badge`, `renderIntake`, `syncIntakeButtons`, `postIntake`, `renderStage` (intake panel / "briefing..." message / brief block), `loadPrefs`, `prefAction` (pin/retire/observations), `togglePrefs`.
- `connect()`: snapshot seeds `intakeByRun` from `msg.intake`; new `intake` event branch; `run` events merge decisions/brief when present. Run selector label shows status (underscores replaced with spaces).
- Task table is hidden while `awaiting_human`, and during `intake`/`briefing` when the run has no tasks; it returns on `running`.

## Interpretations
- Intake summary is read from `run.intake_meta.summary` (present on run rows from SSE snapshot/run events); `/intake` itself does not return it, so before a run row arrives the summary is blank.
- Submit sends only non-locked decisions with a non-empty selection; `reasons` only where the selection differs from a non-model, non-none prefill and the reason is non-blank. Missing required answers are left to the server 400, shown inline.
- Preferences with source `none` (observations but no resolved default) are listed with value "-".
- Brief block is open by default; source column shows `overridden` when the answer differs from a non-model/non-none prefill, else the prefill source.

## URL / body-key cross-check (JS vs API)
- GET /api/events: snapshot keys `runs,tasks,artifacts,intake,verifications`; events `run` (`.run`), `intake` (`.run_id`, `.status`). Matches server.py.
- GET /api/runs/{id}/intake: reads `status,decisions,answers,brief`; decision fields `key,question,why_it_matters,options[value,label,consequences],required,locked,prefill{value,source,evidence{count,projects,exceptions[value,reason]}}`. Matches task-04 shape.
- POST /api/runs/{id}/intake: body `{accept_all:true}` or `{answers:{},reasons:{}}`; 400 `detail` string shown inline. Verified by curl: `{"detail":"unknown decision keys: x"}`.
- GET /api/preferences -> `{key:{value,source,evidence{count,last_seen}}}`; PUT /api/preferences/{key} body `{value}`; DELETE /api/preferences/{key}; GET /api/observations?key= -> rows `value,scope,source,reason,run_id,ts`. Matches prefs.py/server.py.

## Verification
1. node --check on extracted inline script: OK. Met.
2. Mock server 8400 (temp DB, .venv python): page served, `grep -c "Accept all"` = 1. Met.
3. curl end-to-end: POST /api/requests -> intake -> /intake awaiting_human (3 decisions) -> bad key 400 -> {accept_all:true} -> briefing -> done, answers sqlite/MIT/container, brief 456 chars; /api/preferences and /api/observations?key=license return data. Met.
4. Manual checklist below written. No browser was available; the UI was NOT exercised in a browser, only syntax-checked and API-checked.
5. Server stopped (port 8400 free), temp files removed. Repo is not a git repo with commits (no commits yet), so `git status` was not meaningful; only ui/index.html was edited by me.

## Manual checklist (user)
1. Start `PIPEZ_MOCK=1` server, open http://localhost:8400; submit "smoke".
2. Run turns `awaiting human` (pulsing) and the accent-bordered intake panel shows 3 cards with `model` badges and pre-selected defaults; table hidden.
3. Change `storage.database` to another option: reason box does NOT appear (prefill is model). Run three "smoke" runs accepting defaults first; on the fourth, the badge becomes `global (n=3)`; changing it then shows "Why different this time?".
4. Choose "custom..." -> text input appears. Submit with a required decision blank -> 400 text appears inline.
5. Accept all defaults -> "briefing..." then task table returns; Build brief block renders with answers list (overridden shown where applicable).
6. Preferences button -> table lists keys incl. `storage.database` after 3 runs; pin (prompt), retire (confirm), observations link expands rows.

## Left alone
- All backend files, tests, existing task table styling, git.
