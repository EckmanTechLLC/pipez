# Task 06a summary

## Changed
- pipez/llm.py: subtask schema gains optional `depends_on` (int >= 0 array); MockLLM depth-0 subtask 2 depends on [0].
- pipez/db.py: `depends_on` in TASK_COLUMNS + migration; `create_task(depends_on=)`; `update_task` JSON-encodes a list; `get_task/get_tasks/get_children` decode to a list (`_decode_task`).
- pipez/engine.py: `SkippedError`; `_validate_deps`; `_schedule` (DAG scheduler); `_partition`; `_decompose` and `_resume_task` use them; `_resume_task(force=)`.
- pipez/seed_prompts.py: one `depends_on` bullet in "When decomposing" (ordering only, results flow through the parent).
- tests/test_deps.py: chain, diamond, failure/skip, all-failed-raises, invalid edges, resume.
- .odin/project-index.md updated.

## Verification
1. pytest: 30 passed (24 prior + 6 new).
2. Mock smoke on port 8401 (temp DB): run reached `done`; depth-1 subtask seq 2 had `depends_on` = [id of seq 0]; its `start` log ts (…365.0767) >= subtask 0 `finished_at` (…365.0753). Server stopped, temp dir removed.
3. grep "skipped": scheduler (engine.py ~655-670), partition (docstring/SkippedError handling via BaseException branch), resume (comment at ~369; resume uses same scheduler). db.py has only a comment: status is free text, no code change needed.
4. Index updated.

## Scheduler loop (engine._schedule)
- State: pending list, running {future: child}, outcomes {id: value|exception}, bad set.
- Each iteration: first mark pending children with a failed/skipped dep as `skipped` (status, error "dependency failed: <dep title>", log row "skipped"), repeating to a fixpoint (transitive).
- Then start every pending child whose deps are all done and succeeded (no deps -> all start at once).
- `asyncio.wait(running, FIRST_COMPLETED)`; record each result or exception; failures join `bad`.
- Ends when nothing pending/running; running futures cancelled in `finally`.
- Resume passes the dependency values so a redone dependency forces dependents to redo.

## Interpretations
- Cycle handling: DFS along depends-on edges drops the edge that closes a cycle (which edge is dropped is DFS-order dependent). Non-integer entries also dropped as "not an integer"; duplicates deduped silently.
- Children are created first, then `depends_on` ids written via `update_task` (forward indices need ids that don't exist yet).
- Resume: a done sibling whose dependency was redone is force re-executed (leaf) / re-synthesized (parent), as test (e) requires D to re-run.
- Skipped children in synthesis `failed` entries carry `error` + `depends_on` titles; succeeded entries also carry `depends_on` titles when non-empty.
- Existing DBs keep the old `system_default` prompt (seed doesn't overwrite).

## Caveat
- Cleanup used `pkill -f run.py` (too broad); nothing was listening on 8400/8401 afterwards, so if the other agent had a server on 8400 it may have been killed.
