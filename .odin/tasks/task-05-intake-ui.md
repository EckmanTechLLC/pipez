# Task 05: Intake UI — decision form, provenance, brief, preferences panel

## Objective
Make the intake stage usable from the browser. `ui/index.html` (single file,
vanilla JS, SSE, marked + DOMPurify already loaded) gains: run-status styling for
the new states; an intake panel for a run in `awaiting_human` that lists every
decision with its options, pre-filled value, provenance badge, lock/required
markers, custom-value and reason inputs, "Accept all" and "Submit"; the rendered
build brief + decision table once it exists; and a small preferences panel
(resolved defaults with evidence, pin, retire). No server changes — the API from
tasks 03–04 is complete; if something is missing, write it in the summary and
stop rather than adding routes.

## Reference (read first)
- `.odin/sessions/task-04-intake-stage-summary.md` — the exact JSON of `GET /api/runs/{id}/intake`, POST body, and 400 shape
- `.odin/sessions/task-03-preference-store-summary.md` — `/api/preferences`, `/api/observations`, pin/retire
- `pipez/server.py` — routes; SSE `snapshot` (now includes `intake` per run) and the `intake` event
- `ui/index.html` — existing structure: `connect()` (EventSource), `render()`, `renderPanel()`, `treeOrder()`, `submit()`, status classes `.s-*`
- README "Intake" and "Preferences" sections

## Deliverables
1. **Run states.** Status classes and colours for `intake`, `awaiting_human`, `briefing`, `planning` (reserve), `awaiting_plan` (reserve). The run selector label shows the status. `awaiting_human` is visually loud (accent border/pulse) because the system is waiting on the human.
2. **Intake panel** (replaces the task table in the right/main area while the selected run is `awaiting_human`; the task table returns once the run is `running`):
   - Header: run request (first 200 chars), the discovery `summary`.
   - One card per decision: `question` (bold), `why_it_matters` (dim), `required` marker, `locked` → inputs disabled and a "stated in request" badge.
   - Options as radio buttons with `label`; `consequences` shown under the selected option (and on hover for others).
   - Pre-selected from `prefill.value`; a **provenance badge** next to the card title: `request` / `pinned` / `project` / `global (n=count)` / `model` with a tooltip listing `evidence.projects` and `evidence.exceptions` (value + reason).
   - A "custom…" radio revealing a text input.
   - A `reason` text input that appears only when the current selection differs from a non-`model` prefill (this is what makes the override an exception in memory) — label it "Why different this time? (leave blank if this is your new default)".
   - Buttons: **Accept all defaults** (POST `{accept_all: true}`) and **Submit answers** (POST `{answers, reasons}`), disabled while in flight; 400 `detail` shown inline; on success the panel switches to a "briefing…" state until the SSE `run`/`intake` event moves the status on.
3. **Brief view.** For any run with a non-null `brief`: a collapsible "Build brief" block above the task table, markdown-rendered (sanitised), followed by the answers as a compact key → value → source list (source from `decisions[].prefill.source` vs the answer: show `overridden` when they differ).
4. **Preferences panel.** A toggle in the header opens a side panel: `GET /api/preferences` → table key / value / source / evidence count / last seen; per row **pin** (prompts for value, `PUT`) and **retire** (`DELETE`); link "observations" that fetches `/api/observations?key=` into an expandable list (value, scope, source, reason, run, ts). Refresh after each action.
5. **Live updates.** Handle the SSE `intake` event and `run` events so the panel appears/disappears without reload; snapshot `intake` map seeds state on connect.
6. **Keep it one file**, no build step, no new CDN libraries. Keep the existing dark/light-agnostic palette variables. Mobile is not a target.

## Out of scope
- Any backend change (if required, stop and report).
- Plan-stage UI (task 06b), verification/artifact UI changes beyond what exists.
- Styling rewrites of the existing task table.
- Committing to git.

## Verification criteria
1. Extract every inline `<script>` block from `ui/index.html` to a temp file and run `node --check` on it — no syntax errors.
2. Mock server on 8400 with temp DB: `curl -s localhost:8400/ | grep -c "Accept all"` ≥ 1; the page is served with the new markup.
3. Scripted end-to-end through the same API the UI calls (curl): submit → `/intake` shows `awaiting_human` → POST `{accept_all:true}` → `done`, confirming the endpoints/field names used in the JS match (grep the JS for each URL and body key you use and list them in the summary against the API).
4. A manual checklist in the summary for the user (open page, submit "smoke", see three decision cards with `model` badges, change `storage.database` → reason box appears, Accept all → brief renders, Preferences panel shows `storage.database` after 3 runs). You cannot run a browser; say so explicitly.
5. Stop the server, delete temp paths; `git status` shows only `ui/index.html` modified plus the summary.

## Write summary
MANDATORY, last step: `.odin/sessions/task-05-intake-ui-summary.md`: what changed in the page (sections/functions added), the URL/body-key cross-check table, the manual checklist, and anything the API lacked.
