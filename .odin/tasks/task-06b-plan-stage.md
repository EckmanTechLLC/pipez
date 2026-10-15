# Task 06b: Plan stage — architecture, contracts, task DAG, approval gate, plan UI

## Objective
Between the build brief and the build tree, insert a planning phase. The deep
tier turns the brief into an architecture, a module list with explicit
contracts, and a DAG of concrete coding tasks. The run pauses at
`awaiting_plan` until the human approves (or requests changes with a note,
which re-plans). On approval the engine materialises the plan as the task tree:
one integrator root whose children are the plan's tasks, with `depends_on`
edges (task 06a), `kind = "code"`, tier/effort as planned, and the brief +
architecture + contracts as binding context. Code tasks still execute through
the existing LLM path in this task (a sandbox replaces that in 07) — the point
here is the plan, the gate, the tree shape, and the UI.

## Reference (read first)
- `.odin/project-index.md`; summaries for tasks 04 (`_intake`, `answer_intake`, `_brief_and_start`, run phases, `_set_phase`, SSE `intake` event), 05 (UI structure: intake panel, `render()`, SSE handlers, status classes incl. reserved `planning`/`awaiting_plan`), 06a (`_schedule`, `depends_on` resolution by index → id, `skipped`)
- `pipez/engine.py`, `pipez/llm.py` (`INTAKE_SCHEMA`/`BRIEF_SCHEMA` style, `MockLLM.intake/brief`), `pipez/db.py` (run columns + JSON-decoding in `get_run/get_runs`), `pipez/server.py` (`/intake` routes, SSE snapshot), `pipez/seed_prompts.py`, `ui/index.html`
- `tests/test_intake.py` — the end-to-end mock flow to extend

## Design (binding)
- **Run states** gain `planning` and `awaiting_plan`: `briefing → planning → awaiting_plan → running`. `resume()`: `awaiting_plan` → ValueError (400); `planning` → re-run planning.
- **Run columns**: `plan TEXT` (JSON), `plan_meta TEXT` (JSON: usage, model, revisions: [{note, ts}]), `plan_revisions INTEGER DEFAULT 0`.
- **`PLAN_SCHEMA`** (strict, `additionalProperties: false`):
  ```
  { "architecture": str (markdown: components, data flow, key decisions honoured from the brief),
    "stack": [str],
    "repo_layout": str (markdown tree),
    "modules": [ { "name": str, "path": str, "purpose": str,
                   "depends_on": [str],          # module names
                   "contract": str } ],          # public interface, files it owns, how it is tested
    "tasks": [ { "id": str, "title": str, "module": str, "prompt": str,
                 "tier": str (enum = list(config.MODEL_TIERS)), "effort": str? (five-level enum),
                 "depends_on": [str],            # task ids
                 "acceptance": [str] } ],        # checkable statements
    "scaffold_commands": [str] }                 # run once before any task (init repo, toolchain)
  ```
  Required: everything except `effort`. Validate after the call: unknown module names / task ids in `depends_on`, duplicate ids, and cycles are dropped with a `plan_invalid` log entry (never fail the plan for a bad edge); if `tasks` is empty → `LLMError`.
- **Gate API**: `GET /api/runs/{id}/plan` → `{status, plan, plan_meta, plan_revisions}`; `POST /api/runs/{id}/plan` body `{"approve": bool, "note": str?, "plan": object?}`. `approve: true` (optionally with an edited `plan` which is re-validated and replaces the stored one) → materialise. `approve: false` requires a non-empty `note` → status `planning`, revision appended, re-plan with the previous plan and the note in the prompt. Any other status → 400.
- **Materialisation**: root task `role: "integrator"`, `kind: "llm"`, `prompt` = "Integrate the completed modules into the deliverable described by the brief" (+ brief), `context` = brief + `architecture` + `repo_layout` + all module contracts. Children: one per plan task in plan order, `kind: "code"`, `role: "<module>-engineer"`, `title`, `prompt` (task prompt + its module's contract), `tier`, `effort`, `module`, `acceptance` (JSON), `depends_on` resolved task-id → sibling row id (reuse 06a's resolver, generalised to string ids). Then status `running` and the existing `_run` path takes over.
- **Code tasks in this task**: `_execute` checks `task["kind"] == "code"` → log `code_task_placeholder` → run the normal `_decide` path with the `leaf_note` appended so the model completes directly (writes the code/plan as `output` text). No sandbox.
- **Prompt** `plan_architect` (seeded): plan for a team that cannot ask questions; honour every decision in the brief as a constraint; contracts first — each module's public interface and files it owns so tasks can run in parallel; tasks are atomic units one engineer completes against one module in one sitting, with checkable acceptance; `depends_on` only when a task cannot start without another's output; tier rule as in `system_default`; scaffold commands are the minimum to make the repo buildable.
- **Mock**: `MockLLM.plan()` → 2 modules (`core`, `api` depends on `core`), 3 tasks (`t1 core`, `t2 core`, `t3 api` depends on `t1`), one `effort: "low"` on `t2`, 2 scaffold commands. If the prompt contains "REVISION NOTE", prefix the architecture with "Revised: ".
- **UI** (`ui/index.html`): while `awaiting_plan`, a plan panel replaces the task table: architecture (markdown, sanitised), stack chips, repo layout (pre), modules table (name/path/purpose/deps) with contract expanders, tasks table (id/title/module/tier/effort/deps/acceptance count) with acceptance expanders, scaffold commands (pre); buttons **Approve plan** and **Request changes** (textarea for the note, required); inline 400 handling; `planning` shows a spinner line. After approval, the task table shows a `code` badge and the module name on code tasks. SSE `plan` event + snapshot `plan` per run keep it live.
- **Tests** `tests/test_plan.py`: mock end-to-end — accept_all → `awaiting_plan` with 3 tasks; POST `{approve:false}` without note → 400; with note → `planning` → `awaiting_plan`, `plan_revisions == 1`, architecture starts "Revised: "; approve → `running` → `done`; tree: root `integrator` + 3 children `kind == "code"`, `t3.depends_on == [id of t1]`, `t2.effort == "low"`, root `context` contains both contracts; `task_log` of a code task has `code_task_placeholder`; `resume()` on `awaiting_plan` raises.

## Deliverables
1. Schema/db: run columns + decoding; `tasks.kind`, `tasks.module`, `tasks.acceptance` columns (+ `TASK_COLUMNS`, `create_task` kwargs, JSON decode of `acceptance`).
2. `llm.py`: `PLAN_SCHEMA` builder (`plan_schema()` for the dynamic tier enum), `LLM.plan(system, prompt, tier)`, `MockLLM.plan`.
3. `engine.py`: `_plan(run_id)`, validation, `answer_plan(...)`, `_materialise_plan(run_id)`, code-task placeholder in `_execute`, `resume()` rules, SSE `plan` events; `_brief_and_start` now ends in `planning` instead of creating the root.
4. `server.py`: the two `/plan` routes, snapshot includes `plan` per run.
5. `seed_prompts.plan_architect`; README "Plan" section (states, endpoints, what approval does); `.odin/project-index.md`.
6. UI plan panel + code badge; `tests/test_plan.py`; update `tests/test_intake.py` expectations where the flow now stops at `awaiting_plan`.

## Out of scope
- Sandbox execution, git worktrees, Proxmox (07). Mid-tree human pauses (08).
- Changing intake, preference, or decompose/complete prompts.
- Any live LLM call during verification. Committing to git.

## Verification criteria
1. `.venv/bin/python -m pytest -q tests/` passes (30 prior, adjusted, + new).
2. Mock server smoke (8400, temp DB): submit → accept_all → `GET /plan` shows `awaiting_plan` and 3 tasks → POST `{approve:false,note:"tighter"}` → `awaiting_plan` again with `plan_revisions: 1` → POST `{approve:true}` → `done`; `/tasks` shows root `integrator` + 3 `kind: code` children with the expected `depends_on`/`effort`. `curl localhost:8400/ | grep -c "Approve plan"` ≥ 1. Stop server, clean temp paths.
3. `node --check` on the extracted inline script passes.
4. `grep -n "code_task_placeholder" pipez/engine.py` present; `grep -n "plan_architect" pipez/seed_prompts.py` present.

## Write summary
MANDATORY, last step: `.odin/sessions/task-06b-plan-stage-summary.md`: files, trimmed verification output, the example `GET /api/runs/{id}/plan` JSON (trimmed), ambiguities resolved, and a manual UI checklist (you cannot run a browser — say so).
