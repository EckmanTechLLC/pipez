# Task 04b: Intake + plan hardening from the first live run

## Objective
The first live run (run `3ec86c6a6ef8`, request "a self-hosted web tool for
tracking the pipez runs' API spend per project") showed four gaps:
(1) the brief ended with two `open_questions` that were really human
decisions and should have been asked; (2) decision keys are invented per run,
so memory will not match next time unless discovery is shown the keys that
already exist; (3) a locked (request-stated) decision was flagged
`intake_invalid` for having one option; (4) the plan ignored parsimony — 23
tasks, 12 modules, OpenAI adapter + PyInstaller + mypy for a tool whose brief
asked for none of them — and cost 13 min / 90k output tokens on Opus. Fix all
four with prompt + small code changes. Verification is offline (mock + tests).

## Reference
- `.odin/sessions/task-04-intake-stage-summary.md`, `task-06b-plan-stage-summary.md`, `task-04a-schema-hotfix-summary.md`
- `pipez/engine.py`: `_intake`, `answer_intake`, `_brief_and_start`, `_plan`, the `intake_invalid` check
- `pipez/seed_prompts.py`: `intake_discovery`, `intake_brief`, `plan_architect`
- `pipez/prefs.py`: `resolve_all(project_id)` (keys with any observation)
- `pipez/llm.py`: `INTAKE_SCHEMA`, `BRIEF_SCHEMA`, `plan_schema()`; `MockLLM.intake/brief/plan`
- `ui/index.html`: intake card rendering (options radios + "custom…" input), plan panel header
- Live artefacts for taste (read-only; do not modify the live DB): `curl -s localhost:8400/api/runs/3ec86c6a6ef8/intake` and `.../plan` — see what Opus actually produced.

## Deliverables
1. **Known keys into discovery.** `_intake` builds a "Known decision keys" section for the discovery prompt: the keys from `prefs.resolve_all(project_id)` (with their current resolved value and source, so the model sees "storage.database = sqlite (global, n=3)") plus a fixed starter vocabulary in `seed_prompts.py` (`intake_key_vocabulary`: ~20 canonical keys such as `runtime.language, storage.database, auth.method, deployment.packaging, deployment.hosting_model, hosting.target, ui.framework, ui.theme, legal.license, repo.hosting_ci, data.retention, billing.currency, observability.logging, config.format, secrets.source, testing.level, docs.level`). Prompt rule: reuse a known key whenever the decision is the same concept; invent a new dotted key only for genuinely new concepts.
2. **Open questions become decisions.** After the brief call, if `open_questions` is non-empty: create one decision per question with `key: "open.<slug>"`, `question`, `why_it_matters: "The brief could not settle this"`, `options: []`, `proposed_default: ""`, `required: true`, `free_text: true` (new optional flag in the stored decision, not in the LLM schema); append them to `runs.decisions`; set status back to `awaiting_human`; publish `intake`. `answer_intake` accepts free-text answers for `free_text` decisions, records observations (`scope: "request"` — these are per-run facts, not preferences), then re-runs the brief with the answers appended as "Resolved open questions" and continues to planning. Loop at most twice (`PIPEZ_INTAKE_MAX_ROUNDS`, default 2); on the last round, remaining open questions are written into the brief as explicit assumptions instead. `intake_brief` prompt: only emit an open question when no option could reasonably be proposed; otherwise propose a default and list it as a decision for the human.
3. **Locked exemption.** Decisions with `locked: true` are exempt from the `< 2 options` → `intake_invalid` check.
4. **Plan parsimony.** `PIPEZ_PLAN_MAX_TASKS` (default 12) and `PIPEZ_PLAN_MAX_MODULES` (default 8) injected into the `plan_architect` prompt as hard limits ("merge small tasks; a task is a unit one engineer completes in one sitting, not a function"). Prompt additions: no tooling, packaging, linters, type-checkers, adapters, or integrations that the brief did not decide — if you believe one is needed, put it under an `OPTIONS` heading in `architecture`, not in modules/tasks; prefer the standard library when the brief does not name a framework. Code-side: if the plan exceeds either limit, keep it but log `plan_oversize` and store `plan_meta.warnings`; the UI plan panel shows the warning line and the plan's cost line (`model · output_tokens · seconds` from `plan_meta`). The `effort` for planning becomes config `PIPEZ_PLAN_EFFORT` (default `high`) applied over the deep tier's effort.
5. **UI**: free-text decision cards render a textarea instead of radios; plan panel warning/cost lines (deliverable 4). `node --check` must pass.
6. **Mock + tests**: `MockLLM.brief` returns one open question on the first call for a run and none on the second (track per prompt content: if "Resolved open questions" is in the prompt → none). `MockLLM.intake` echoes any "Known decision keys" it sees by reusing `storage.database` (already does). Tests: open-question round trip (awaiting_human twice, free-text answer recorded as `scope: request`, brief regenerated, then `awaiting_plan`); locked exemption; known-keys section present in the discovery prompt when observations exist (capture the prompt via a stub LLM); `plan_oversize` logged when a stub plan has 13 tasks. Update existing tests that assumed a single intake round.

## Out of scope
- Sandbox/coding (07c). Changing the decision/plan JSON schemas beyond what is stated. Touching port 8400 or the live DB.
- Committing to git.

## Verification criteria
1. `.venv/bin/python -m pytest -q tests/` passes.
2. Mock server smoke on **8401**: submit → accept_all → `awaiting_human` again with one `open.*` free-text decision → POST answer → `awaiting_plan`; `GET /api/runs/{id}` `brief` contains "Resolved open questions". Stop 8401 (`fuser -k 8401/tcp`), clean temp paths.
3. `grep -n "intake_key_vocabulary\|PLAN_MAX_TASKS" pipez/seed_prompts.py pipez/config.py pipez/engine.py` all present.
4. `node --check` on the inline script passes.

## Write summary
`.odin/sessions/task-04b-intake-plan-hardening-summary.md`: files, verification output, the final text of the three changed prompts' new paragraphs (so the controller can review wording).
