# Task 06b summary

## Changed
- pipez/db.py: runs.plan/plan_meta/plan_revisions, tasks.kind/module/acceptance (migrations, TASK_COLUMNS, create_task kwargs, acceptance JSON decode, update_task encodes lists); `set_plan`; run JSON decode includes plan/plan_meta.
- pipez/llm.py: `plan_schema()` (dynamic tier enum, effort optional), `LLM.plan`, `MockLLM.plan` (2 modules, 3 tasks, t2 effort low, "Revised: " on REVISION NOTE).
- pipez/engine.py: `_brief_and_start` ends in `planning`; `_plan`, `_validate_plan`, `_break_cycles` (generic; `_validate_deps` now uses it), `answer_plan`, `_materialise_plan`, `_publish_plan`, `_set_plan_phase`; code-task placeholder + forced leaf note; `resume()` rules.
- pipez/server.py: GET/POST /api/runs/{id}/plan, snapshot `plan`.
- pipez/seed_prompts.py: `plan_architect`. README "Plan" section. .odin/project-index.md.
- ui/index.html: plan panel (architecture md, stack chips, layout, modules/tasks tables with expanders, scaffold, note textarea, Approve plan / Request changes, inline errors), planning message, `code` badge + module name, SSE `plan` handling.
- tests/test_plan.py (5 tests); tests/test_intake.py adjusted (flow stops at awaiting_plan; root context startswith brief).

## Verification
1. pytest: 35 passed (30 prior + 5 new). Met.
2. Mock smoke on 8400 (temp DB): awaiting_plan with 3 tasks; approve:false without note -> 400 "a note is required to request changes"; with note -> planning -> awaiting_plan, plan_revisions 1, architecture "Revised: ..."; approve -> running -> done. Tasks: integrator/llm root + core-engineer code (t1), core-engineer code effort low (t2), api-engineer code depends_on [Core data model]. grep -c "Approve plan" = 1. Server stopped (fuser -k), port free, temp dir removed. Met.
3. node --check on extracted inline script: ok. Met.
4. grep: code_task_placeholder at engine.py:737; plan_architect at seed_prompts.py:182. Met.

## Example GET /api/runs/{id}/plan (trimmed)
{"status":"awaiting_plan","plan_revisions":1,"plan_meta":{"tier":"deep","model":"mock","revisions":[{"note":"tighter","ts":...}]},
 "plan":{"architecture":"Revised: Two components: core ... and api ...","stack":["python","fastapi","sqlite"],
  "repo_layout":"app/\n  core/\n  api/\ntests/",
  "modules":[{"name":"core","path":"app/core","depends_on":[],"contract":"CONTRACT-CORE: ..."},{"name":"api","depends_on":["core"],...}],
  "tasks":[{"id":"t1","module":"core","tier":"standard","depends_on":[],...},{"id":"t2","effort":"low",...},{"id":"t3","module":"api","depends_on":["t1"],...}],
  "scaffold_commands":["git init","python -m venv .venv"]}}

## Interpretations
- Approved tree runs via `_run_resume`/`_resume_task` (root already has children, so no decision call for the integrator; it goes straight to scheduling then synthesis). This is the "existing path" that handles pre-built children.
- Plan validation: unknown tiers fall back to `deep` (logged); tasks/modules missing id/name or duplicated are dropped; edited plan with no tasks on approve -> 400 and gate stays open. Invalid-plan log goes to task_log `run:<id>`.
- Revision re-plan includes previous plan + latest note only; failure during planning marks the run failed (resume re-plans).
- Acceptance is stored in tasks.acceptance only, not appended to the task prompt (spec says prompt = task prompt + contract).
- Code tasks force the leaf note regardless of depth; a real model could still choose decompose (not guarded; sandbox task 07 replaces this).
- Resume of a failed run with brief but no tasks now re-plans instead of redoing intake (small addition to cover planning failures).
- Existing DBs keep any previously seeded prompts; plan_architect is new so it seeds.

## Not run
- No browser available; no live LLM calls; no git commit.

## Manual UI checklist (not exercised, no browser)
- Submit, accept defaults: planning spinner line appears, then plan panel replaces task table at awaiting_plan.
- Panel shows architecture (rendered markdown), stack chips, layout pre, modules table with contract expanders, tasks table with acceptance-count expanders, scaffold commands.
- Request changes with empty note: inline error, no request; with note: panel disappears, "planning… revision 1", new plan appears with the note cleared.
- Approve plan: task table returns; code tasks show a `code` tag and module name; run reaches done.
- Reload during awaiting_plan: panel restored from snapshot; typed note kept across re-renders.
