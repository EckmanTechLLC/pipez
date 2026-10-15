# Task 06a: Dependency edges between sibling tasks + DAG scheduling

## Objective
Let a decomposition declare that some subtasks depend on others. Today
`_decompose` runs all children with one `asyncio.gather`; after this task,
children run as soon as their declared dependencies are `done`, dependents of a
failed task are marked `skipped` (treated as failed for synthesis), and
everything still respects the global concurrency semaphore. This is the
engine-level prerequisite for the plan stage (06b) and for code tasks whose
modules build on each other (07). Pure engine/schema/db/tests work; no prompts
beyond one sentence, no UI.

## Reference (read first)
- `.odin/project-index.md`; `.odin/sessions/task-02-providers-local-tier-summary.md` (`_llm_call`, escalation), `task-04-intake-stage-summary.md` (run phases — do not disturb)
- `pipez/engine.py` — `_decompose` (child creation + gather + partition + synthesize), `_execute`, `_resume_task` (resume walks children with gather too), `_update`, `db.log`
- `pipez/llm.py` — `decision_schema()` subtask item (`role,title,prompt,tier,effort?`); `MockLLM.decide`
- `pipez/db.py` — `TASK_COLUMNS`, `MIGRATIONS`, `create_task`, `get_children` (ordered by `seq`)
- `pipez/seed_prompts.py` — `system_default` "When decomposing" bullets
- `tests/test_escalation.py` — engine test harness pattern (temp DB, stub LLM)

## Design (binding)
- Schema: each subtask may carry `"depends_on": [int, ...]` — indices into the same `subtasks` array (0-based). Optional, default `[]`. Self-references, out-of-range indices, and cycles are invalid: log `deps_invalid` with details and **drop the offending edges** (never fail the decomposition for a bad edge).
- Storage: `tasks.depends_on TEXT` (JSON list of sibling task **ids**, resolved at creation). New status value `skipped`.
- Scheduling in `_decompose` (and the same for `_resume_task`): maintain `pending`, `running`, `done`, `failed/skipped` sets. Loop: start every pending child whose deps are all `done`; `await` the first completion (`asyncio.wait(..., return_when=FIRST_COMPLETED)`); on failure, mark every transitive dependent `skipped` with `error = "dependency failed: <title>"` and `db.log(child, "skipped", {...})`; continue until no pending/running remain. Children with no deps and the no-deps case overall must behave exactly as today (all started immediately).
- Partition for synthesis: `skipped` children go into `failed` with their error string; "all subtasks failed" still raises.
- The task tree ordering (`seq`) is unchanged; `get_children` stays ordered by `seq`.
- `ExecutionContext`-style ordering info for the synthesizer: include each child's `depends_on` titles in the `succeeded`/`failed` entries so the parent sees the structure.

## Deliverables
1. `decision_schema()` subtask gains optional `depends_on` (array of integers ≥ 0). `MockLLM.decide` at depth 0 makes subtask 2 depend on subtask 0 (so the mock tree exercises one edge; everything else unchanged).
2. `db.py`: migration `ALTER TABLE tasks ADD COLUMN depends_on TEXT`, column in `TASK_COLUMNS`, `create_task(..., depends_on=None)`; `get_task`/`get_children` return `depends_on` JSON-decoded as a list.
3. `engine.py`: edge validation + resolution to ids at child creation; the dependency scheduler for `_decompose` and `_resume_task`; `skipped` handling; synthesis entries carry `depends_on` titles. `_publish_task` already streams every `_update`, so the UI sees `skipped` without changes.
4. `seed_prompts.system_default` "When decomposing": add one bullet — `Subtasks run in parallel unless you add depends_on (indices of earlier subtasks whose results this one needs); use it only when a subtask genuinely cannot start without another's output — it serialises work.` Note: since siblings still "cannot see each other", a dependent subtask does NOT automatically receive its dependency's result in this task; the parent's shared_context remains the only channel. State this in the bullet ("ordering only; results still flow through you").
5. `tests/test_deps.py`: (a) chain A→B→C runs strictly in order (record start/finish times with a stub LLM that sleeps); (b) diamond A, B∥C after A, D after B and C — B and C overlap, D starts after both; (c) A fails → its dependents `skipped` with the error text, unrelated siblings still run, parent synthesises with the skipped ones listed under failed; (d) invalid edges (self, out-of-range, cycle) are dropped with a `deps_invalid` log row and the run completes; (e) resume: mark B `failed` in the DB after a completed diamond run, call `resume`, assert only B and D re-execute and A/C return stored results.

## Out of scope
- Plan stage, approval gate, UI (06b). Sandbox/code execution (07).
- Passing dependency results into dependents' prompts (07/08 decide how code tasks share state).
- Changing concurrency limits or the semaphore.
- Committing to git.

## Verification criteria
1. `.venv/bin/python -m pytest -q tests/` passes (24 prior + new).
2. Mock server smoke (8400, temp DB): submit, accept_all, run reaches `done`; `/api/runs/{id}/tasks` shows the depth-1 subtask with `depends_on` = `[<id of subtask 0>]` and `task_log` for that subtask has `start` timestamp ≥ subtask 0's `finished_at`. Stop server, clean temp paths.
3. `grep -n "skipped" pipez/engine.py pipez/db.py` shows the status handled in scheduler, partition, and resume.
4. `.odin/project-index.md` updated for the new column/schema field/test file.

## Write summary
MANDATORY, last step: `.odin/sessions/task-06a-task-dependencies-summary.md`: files, trimmed verification output, the scheduler's loop described in ≤10 lines, ambiguities resolved.
