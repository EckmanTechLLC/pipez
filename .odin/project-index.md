# pipez project index

Hierarchical task-decomposition engine (seeded from aimee-v2). Entry: `run.py` (uvicorn, port 8400). UI: `ui/index.html`.

## pipez/__init__.py
Empty package marker.

## pipez/config.py
Env-driven config (`PIPEZ_*`); loads `.env` from project root first.
- `MODEL_TIERS` - tier name -> {provider, model, effort} for deep/standard/light.
- `DEFAULT_TIER` - "deep".
- Scalars: MAX_TOKENS, TASK_RETRIES, RETRY_BASE_DELAY, ENABLE_FALLBACKS, MOCK, ENABLE_SEARCH, SEARCH_MAX_USES, MAX_DEPTH, MAX_CHILDREN, MAX_TASKS_PER_RUN, MAX_CONCURRENCY, ENABLE_ARTIFACTS, DB_PATH, ARTIFACTS_DIR, HOST, PORT.

## pipez/providers.py
Provider layer; the only module importing `anthropic`.
- `LLMError`, `ProviderTransientError` (subclass; transport/5xx), `is_transient(e)` - retry classifier.
- `Provider.complete(system, prompt, schema, tier, *, fetch=False)` -> (json, usage).
- `AnthropicProvider` - streaming `_create`, fallbacks gate, web tools, pause_turn loop, refusal/truncation errors; `render_artifacts` (Anthropic-only).
- `OpenAICompatProvider` - httpx `/v1/chat/completions` with json_schema response_format, strips `<think>`; `healthy()` probes `/v1/models`.

## pipez/llm.py
Schema facade over providers.
- `RESULT_SCHEMA`, `VERDICTS_SCHEMA`, `FACTS_SCHEMA`; `decision_schema()` builds the decision schema with the live tier enum and optional per-subtask `effort`. Re-exports `LLMError`.
- `LLM(providers=None)` - owns one provider per name, dispatches by `tier["provider"]`.
  - `decide`, `synthesize` - any tier; `verify`, `render_artifacts` - require an Anthropic tier.
  - `_call` - slow-retry loop (transient + truncation); `_resolve_tier`.
- `MockLLM` - deterministic no-API stand-in (sets `effort: low` on one depth-0 subtask).
- `make_llm()` - MockLLM if config.MOCK else LLM.

## pipez/config.py
Env-driven config (`PIPEZ_*`); loads `.env` from project root first.
- `MODEL_TIERS` - tier name -> {provider, model, effort}; `local` (openai_compat) added only if `PIPEZ_LOCAL_BASE_URL` set.
- `DEFAULT_TIER`, `TIER_ORDER`, `next_tier_up(name)`, `ESCALATE_ON_FAILURE`.
- `LOCAL_BASE_URL/MODEL/API_KEY/TIMEOUT/TEMPERATURE/THINKING`.
- Scalars: MAX_TOKENS, TASK_RETRIES, RETRY_BASE_DELAY, ENABLE_FALLBACKS, MOCK, ENABLE_SEARCH, SEARCH_MAX_USES, MAX_DEPTH, MAX_CHILDREN, MAX_TASKS_PER_RUN, MAX_CONCURRENCY, ENABLE_ARTIFACTS, DB_PATH, ARTIFACTS_DIR, HOST, PORT.

## pipez/engine.py
- `Engine(db, bus, llm)` - runs task trees.
  - `submit(request, context, ...)` - create run and start it.
  - `resume(run_id)` - restart unfinished tasks of a run.
  - `verify(run_id)` - per-section and cross-section fact verification (deep tier).
  - `rerender(run_id)` - re-run artifact rendering.
  - `_render_artifacts` - render deliverable files (standard tier).
  - `_llm_call` - resolves tier (+ per-task effort), local-down fallback, escalation on `LLMError`; `_probe_local` - one-shot local health probe at init.
  - `_execute`, `_decide`, `_decompose`, `_synthesize` - per-node lifecycle.
  - `_role_prompt`, `_system_prompt`, `_task_prompt`, `_context_chain`, `_ancestry` - prompt assembly.
  - `_update`, `_publish_task`, `_publish_run` - persist and emit events.

## pipez/db.py
- `new_id()` - short random id.
- `Database(path)` - SQLite (WAL) persistence: runs (`create_run`, `set_run_status`, `finish_run`, `get_runs`, `get_run`, `count_run_tasks`), tasks (`create_task` with `effort`, `update_task`, `add_tokens`, `get_task`, `get_tasks`, `get_children`), `log`/`get_log`, artifacts (`add_artifact`, `get_artifacts`), verifications (`add_verification`, `get_verifications`, `clear_verifications`), prompts (`get_prompt`, `set_prompt`, `get_prompts`, `seed_prompt`).

## pipez/events.py
- `EventBus` - in-process pub/sub (`subscribe`, `unsubscribe`, `publish`) feeding SSE.

## pipez/seed_prompts.py
- `seed(db)` - inserts default role/system/verifier/render prompts if missing.

## pipez/server.py
FastAPI app "PIPEZ"; models `SubmitRequest`, `PromptBody`; `startup()` builds the Engine.
Routes:
- POST /api/requests - submit a run
- GET /api/runs - list runs
- GET /api/runs/{run_id}/tasks - task tree rows
- GET /api/runs/{run_id}/artifacts - artifact rows
- POST /api/runs/{run_id}/render - re-render artifacts
- POST /api/runs/{run_id}/resume - resume run
- GET /api/runs/{run_id}/verifications - verification rows
- POST /api/runs/{run_id}/verify - run verification
- GET /artifacts/{run_id}/{filename} - download artifact
- GET /api/tasks/{task_id}/log - task log
- GET /api/prompts - list prompts
- PUT /api/prompts/{name} - set prompt
- GET /api/events - SSE stream (snapshot first)
- GET / - UI

## tests/
pytest suite (`requirements-dev.txt`: pytest, pytest-asyncio). `test_providers.py` - OpenAI-compat provider via `httpx.MockTransport`, `decision_schema` tier enum; `test_escalation.py` - engine escalation local -> light with stub providers.

## pipez/prefs.py
- `PreferenceStore(db)` - observations + per-key resolution (request > pinned > project > learned global > none). `observe`, `observations`, `resolve`, `resolve_all`, `pin`, `retire`, `learned_default` (graduation: `PREF_WINDOW`/`PREF_GRADUATE`). No LLM calls.
- db.py additions: `runs.project_id` (+ backfill), `observations`, `preferences` tables.
- Routes added in server.py: POST/GET /api/observations; GET /api/preferences; GET/PUT/DELETE /api/preferences/{key}; GET /api/runs/{run_id}.

## Intake (task 04)
- engine.py: `Engine(db, bus, llm, prefs=None)`; `submit()` now creates a run in `intake`; `_intake` (discovery + prefill), `answer_intake` (validate, observe, -> briefing), `_brief_and_start` (brief, root task with `context=brief`, -> running), `_run_llm_call` (run-level tier escalation, logs to task_log `run:<id>`), `_decision_table`, `_set_phase`. `resume()` handles `awaiting_human`/`intake`/`briefing`.
- llm.py: `INTAKE_SCHEMA`, `BRIEF_SCHEMA`, `LLM.intake/brief`, `MockLLM.intake/brief`.
- db.py: `runs.decisions/answers/brief/intake_meta`; `set_intake`, `set_answers`, `set_brief`; `create_run(status=)`; run JSON columns decoded in `get_run/get_runs`.
- server.py: GET/POST /api/runs/{run_id}/intake; SSE snapshot `intake`; event `{type: intake}`.
- seed_prompts.py: `intake_discovery`, `intake_brief`. tests/test_intake.py.

## Task dependencies (task 06a)
- llm.py: `decision_schema()` subtasks accept optional `depends_on` (int indices into the same array); `MockLLM` depth-0 subtask 2 depends on 0.
- db.py: `tasks.depends_on` (JSON list of sibling task ids; decoded to a list by `get_task/get_tasks/get_children`); `create_task(depends_on=)`; `update_task` accepts a list. New task status `skipped`.
- engine.py: `_validate_deps` (drops self/out-of-range/cycle edges, logs `deps_invalid`), `_schedule` (DAG scheduler shared by `_decompose` and `_resume_task`; failed -> transitive dependents `skipped`), `_partition` (succeeded/failed entries with `depends_on` titles), `SkippedError`. `_resume_task(force=)` redoes dependents of redone siblings.
- seed_prompts.py: `system_default` bullet on `depends_on` (ordering only). tests/test_deps.py: chain, diamond, skip, all-failed, invalid edges, resume.

## Plan stage (task 06b)
- engine.py: `_brief_and_start` now ends in `planning`; `_plan` (deep tier, revision-aware), `_validate_plan` (drops bad refs, logs `plan_invalid` on `run:<id>`), `_break_cycles` (shared with `_validate_deps`), `answer_plan` (gate: approve / request changes), `_materialise_plan` (integrator root + `kind=code` children), `_publish_plan`, `_set_plan_phase`. `_execute` logs `code_task_placeholder` for code tasks and forces the leaf note. `resume()`: `awaiting_plan` -> ValueError, `planning` -> re-plan.
- llm.py: `plan_schema()`, `LLM.plan`, `MockLLM.plan`. db.py: `runs.plan/plan_meta/plan_revisions`, `tasks.kind/module/acceptance`, `set_plan`.
- server.py: GET/POST /api/runs/{run_id}/plan; SSE snapshot `plan`; event `{type: plan}`. seed_prompts.py: `plan_architect`. ui/index.html: plan panel (`renderPlan`, `postPlan`), code badge + module name. tests/test_plan.py.

## Sandboxes (task 07b)
- pve.py: `ProxmoxClient` (httpx2, optional `transport`), `PveError`, `PveTransientError`; `free_vmid`, `pool_members`, clone/wait_task/set_config/start/stop/destroy/status/agent_interfaces.
- sandbox.py: `Sandbox` ABC, `PveSandbox` (ssh via subprocess), `MockSandbox` (temp dir), `compose_command` (secret env -> `.env`), `make_sandbox`, `SandboxError`.
- db.py: `sandboxes` table, `upsert_sandbox/get_sandbox/list_sandboxes`. config.py: `PVE_*`, `SANDBOX*`, `SSH_KEY` (`PVE_AUTH_HEADER` holds the token; secret only in config).
- engine.py: `Engine(..., sandbox_factory=None)`, run state `provisioning`; `_run_with_sandbox` (acquire/reuse -> run -> release), `_acquire_sandbox`, `_release_sandbox`, `destroy_sandbox`. Approve and resume of planned runs go through it.
- server.py: GET /api/sandboxes, GET /api/runs/{id}/sandbox, POST /api/sandboxes/{run_id}/destroy; SSE `sandbox` + snapshot. ui: sandbox badge + destroy button.
- tests: test_pve.py, test_sandbox.py, test_plan.py (lifecycle); conftest forces mock sandboxes.

## Coding leaves (task 07c)
- infra/runner/: `pipez_runner.py` (Claude Agent SDK `query`, one JSON result on stdout), `mock_runner.py` (same contract, real git, no SDK), README.md.
- engine.py: `_init_workspace` (repo, scaffold, `integration`, runner + .env upload), `_execute_code_task` (worktree off integration, `.pipez/task.md`, runner, policy, merge under `_merge_lock`), `_task_md`, `_leaf_model`, `ScaffoldError`; `_execute(task, dep_values)` routes `kind=code` there; `_run_with_sandbox` inits the workspace and keeps the sandbox when any task failed.
- sandbox.py: `Sandbox.git(args, cwd)`, `runner_script`/`python` per sandbox kind; `MockSandbox` honours `PIPEZ_SANDBOX_KEEP`. db.py: `tasks.cost_usd`, `add_cost`. config.py: `ANTHROPIC_API_KEY`, `LEAF_*`, `SANDBOX_KEEP`. tests/test_code_leaf.py.

## Integration and delivery (task 07d)
- engine.py: `_integrate(run_id, root)` (clean tree, build/test via `bash -lc`, ff-only main, tag `pipez/<run_id>`, tar.gz + bundle exported to `ARTIFACTS_DIR/<run_id>/`; logs `integrate_build/test/ok/failed`), `_integration_section` (root synthesis input); called from `_resume_task` for the root before synthesis. `_run_resume` skips `_render_artifacts` for runs with a plan. `_validate_plan` normalises `build_command`/`test_command`.
- llm.py: `plan_schema()` optional `build_command`/`test_command`; MockLLM returns `true` / `python3 -m pytest -q`. seed_prompts.py: plan_architect sentence. config.py: `INTEGRATE_TIMEOUT`.
- ui/index.html: `codeStats` (cost/tests/files per code row), `integrationFailure`, `runIt`. tests/test_integrate.py.

## Control and fix loops (task 08)
- engine.py: `stop(run_id)` + `_mark_cancelled` (drivers registered in `_run_tasks` via `_spawn(coro, run_id)`; `_run_with_sandbox` wraps `_run_planned` and keeps the sandbox on `CancelledError`; `_schedule` awaits cancelled children); `_ask_human` / `answer_task` (task status `awaiting_human`, `tasks.question` JSON, `_waiters` events, run `awaiting_human`); `_execute_code_task` loops over retries (`leaf_retry`), questions and fresh worktrees, fixer tasks (module `*`) use a detached worktree and ff-merge; `_integrate_with_fixes` + `IntegrationError` (fix loop, `PIPEZ_FIX_ROUNDS`); `BASELINE_GITIGNORE` in `_init_workspace`; `resume()` handles `cancelled`.
- db.py `tasks.question`; sandbox.py `_spawn` kills the child on cancel; config `LEAF_RETRIES`, `FIX_ROUNDS`.
- server.py: POST /api/runs/{id}/stop, POST /api/runs/{id}/tasks/{task_id}/answer. ui/index.html: Stop button, "Waiting on you" cards (`renderWaiting`), `cancelled` status.
- infra/runner: `question.json` -> `needs_human` (real + mock); mock env `ASK/FLAKY/NOTESTS/FIXMARK/SLEEP`. tests: test_stop.py, test_control.py.

## Deploy to a lab VM (task 09)
- deploy.py: `Deployer` (provision -> upload tarball -> extract -> install commands -> systemd unit -> health check; reports via callbacks), `systemd_unit`, `app_name`, `make_deploy_sandbox` (PveSandbox from template 9000 / VMIDs 9200-9299 / `pipez-app-<run>` / 2c 2G, or `MockDeploySandbox`: temp dir + background process).
- sandbox.py: `PveSandbox(template=, vmid_range=, name_prefix=, cores=, memory=)`, defaults unchanged.
- engine.py: `Engine(..., deploy_factory=)`, `_clean_deploy` (in `_validate_plan`), `_wants_deploy`, `deploy`, `_deploy`, `undeploy`, `_abort_deploy`; run status `deploying` set at the end of `_run_resume`, deploy runs in `_run_planned` after the builder sandbox is released; `stop()` handles `deploying`.
- llm.py: `plan_schema()` optional `deploy`; MockLLM plan deploys a python http.server. seed_prompts.py: plan_architect `deploy` bullet. config.py: `DEPLOY_AUTO`, `DEPLOY_TEMPLATE`, `DEPLOY_VMID_RANGE`, `DEPLOY_HEALTH_TIMEOUT`.
- db.py: `deployments` table, `upsert_deployment/get_deployment/list_deployments/replace_deployment`. server.py: GET /api/runs/{id}/deployment, GET /api/deployments, POST /api/runs/{id}/deploy, POST /api/runs/{id}/undeploy; SSE `deployment` + snapshot. ui: `deployBadge`, plan-panel deploy block.
- tests/test_deploy.py; conftest sets `DEPLOY_AUTO=False` (deploy tests opt in).


## Acceptance review (task 13a)
- engine.py: planned-run order is run -> `_deploy(review=True)` -> `_review` -> `_finish_review` (run `done`, sandbox destroyed unless build/review failed). `_review` (reviewer leaf, role `REVIEW_ROLE`, tier `VERIFY_TIER`, detached worktree at `integration`, runner `--review`), `_review_md`, `reverify`/`_reverify`, `_review_skipped`, `_build_tasks`; reviewer rows are ignored by `resume`/`_resume_task`. `stop()` handles `verifying` for plan runs.
- infra/runner: `--review` mode (`.pipez/verdicts.json`, dirty-tree refusal, `verdicts` in stdout); mock emits 2 confirmed or 1 wrong (`MOCK_WRONG`).
- db.py `runs.verification`, `set_run_verification`; config `VERIFY_AUTO/VERIFY_TIER`; seed prompt `acceptance_reviewer`; POST /api/runs/{id}/verify re-runs the review on plan runs; ui badge `reviewBadge`. tests/test_review.py.
