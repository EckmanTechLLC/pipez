# pipez

pipez is a decision-first, end-to-end software builder on a hierarchical task engine: a request becomes a task tree where each node makes one structured decision (decompose into parallel subtasks, or complete directly), every transition is persisted to SQLite and streamed to a live UI. Seeded from aimee-v2.

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                # set ANTHROPIC_API_KEY, or use PIPEZ_MOCK=1
.venv/bin/python run.py             # http://127.0.0.1:8400
```

Keyless dry run: `PIPEZ_MOCK=1 .venv/bin/python run.py`

Stop: `fuser -k 8400/tcp`

## Config (env)

| Var | Default | |
|---|---|---|
| `ANTHROPIC_API_KEY` | | API key |
| `PIPEZ_MODEL_DEEP` / `PIPEZ_EFFORT_DEEP` | `claude-opus-5-5` / `high` | deep tier |
| `PIPEZ_MODEL_STANDARD` / `PIPEZ_EFFORT_STANDARD` | `claude-sonnet-5-5` / `medium` | standard tier |
| `PIPEZ_MODEL_LIGHT` / `PIPEZ_EFFORT_LIGHT` | `claude-haiku-5-5` / `low` | light tier |
| `PIPEZ_LOCAL_BASE_URL` / `PIPEZ_LOCAL_MODEL` | unset | OpenAI-compatible endpoint (vLLM); the `local` tier exists only when the URL is set |
| `PIPEZ_LOCAL_API_KEY` | empty | optional bearer token for the local endpoint |
| `PIPEZ_LOCAL_TIMEOUT` / `PIPEZ_LOCAL_TEMPERATURE` | 600 / 0.2 | local call timeout (s), sampling temperature |
| `PIPEZ_LOCAL_THINKING` | 0 | 1 = enable qwen3 thinking (`enable_thinking`) |
| `PIPEZ_ESCALATE` | 1 | on a failed call, retry on the next tier up (local, light, standard, deep) |
| `PIPEZ_INTAKE_TIER` | `deep` | tier of the intake discovery call (invalid names fall back to `deep`) |
| `PIPEZ_MAX_TOKENS` | 128000 | per-call output cap |
| `PIPEZ_TASK_RETRIES` / `PIPEZ_RETRY_DELAY` | 3 / 20 | transient-failure retries, base delay (s) |
| `PIPEZ_FALLBACKS` | 1 | server-side refusal fallbacks |
| `PIPEZ_MOCK` | 0 | 1 = no API, canned tree |
| `PIPEZ_SEARCH` / `PIPEZ_SEARCH_MAX_USES` | 1 / 5 | web search tool |
| `PIPEZ_MAX_DEPTH` | 4 | tree depth cap |
| `PIPEZ_MAX_CHILDREN` | 8 | per-node fan-out cap |
| `PIPEZ_MAX_TASKS` | 100 | per-run task cap |
| `PIPEZ_CONCURRENCY` | 4 | parallel LLM calls |
| `PIPEZ_ARTIFACTS` | 1 | render deliverable files |
| `PIPEZ_LEAF_MAX_TURNS` / `PIPEZ_LEAF_MAX_BUDGET_USD` | 60 / 5.0 | per code task Agent SDK limits |
| `PIPEZ_LEAF_TIMEOUT` / `PIPEZ_LEAF_REQUIRE_TESTS` | 1800 / 1 | runner timeout (s); fail a task whose tests fail |
| `PIPEZ_LEAF_RETRIES` / `PIPEZ_FIX_ROUNDS` | 1 / 2 | extra attempts for a failed code task; fixer rounds when integration fails |
| `PIPEZ_INTEGRATE_TIMEOUT` | 1800 | timeout (s) for each integrator build / test command |
| `PIPEZ_DEPLOY_AUTO` / `PIPEZ_DEPLOY_HEALTH_TIMEOUT` | 1 / 180 | auto-deploy after a successful VM-hosted run; health wait (s) |
| `PIPEZ_DEPLOY_TTL_HOURS` / `PIPEZ_DEPLOY_SWEEP_SECONDS` / `PIPEZ_DEPLOY_REPLACE` | 72 / 3600 / 1 | ready deployments are undeployed after this many hours unless kept (0 = never); expiry pass interval (s); a newly ready deployment undeploys its project's other ready, non-kept ones (`PUT /api/runs/{id}/deployment/keep`, `POST /api/deployments/expire`) |
| `PIPEZ_PVE_DEPLOY_TEMPLATE` / `PIPEZ_PVE_DEPLOY_VMID_RANGE` | 9000 / 9200-9299 | template (plain Ubuntu, not the builder) and VMIDs for deploy VMs; set `PIPEZ_PVE_DEPLOY_TEMPLATE=9002` (pipez-app-base, guest agent preinstalled; build with `infra/pve/make-app-template.sh`) to avoid the >240 s apt wait |
| `PIPEZ_SANDBOX_KEEP` | 0 | 1 = mock sandbox temp dirs survive teardown (debug) |
| `PIPEZ_DB` | `./pipez.sqlite3` | |
| `PIPEZ_ARTIFACTS_DIR` | `./artifacts` | |
| `PIPEZ_HOST` / `PIPEZ_PORT` | `127.0.0.1` / 8400 | |

## Preferences

pipez remembers answers so the intake stops asking what it already knows. Each human decision is stored as an append-only **observation** (`key`, `value`, `scope` request/project/global, `source` intake/request/override, optional `reason`). A **project** is a run lineage (`runs.project_id`: a root run's own id, inherited by runs submitted with `parent_run_id`).

Resolution order for a key (first hit wins; `evidence` is always returned):

1. value stated in the current request (`source: request`)
2. pinned preference (`PUT /api/preferences/{key}`, `source: pinned`)
3. the project's latest observation (`source: project`)
4. learned global default (`source: global`)
5. nothing (`source: none`) - the intake must ask

Graduation: take the latest `PIPEZ_PREF_WINDOW` observations for the key across all projects, excluding exceptions (`source=override` with a non-empty `reason`) and request-scope observations. If one value has at least `PIPEZ_PREF_GRADUATE` of them and is the strict plurality, it becomes the global default. Exceptions are listed in `evidence.exceptions` but do not erode the default. `DELETE /api/preferences/{key}` retires the key: the global default stays suppressed until a newer observation arrives.

| Var | Default | |
|---|---|---|
| `PIPEZ_PREF_WINDOW` | 5 | observations considered for a default |
| `PIPEZ_PREF_GRADUATE` | 3 | agreeing observations needed |

Keys are freeform lowercase dotted strings; values are strings. Examples: `storage.database`, `license`, `hosting.target`, `ui.theme`.

API: `POST/GET /api/observations`, `GET /api/preferences?project_id=`, `GET|PUT|DELETE /api/preferences/{key}`, `GET /api/runs/{run_id}`.

## Intake

Every run starts with a decision-first intake (no root task exists until it finishes). Run states: `intake` -> `awaiting_human` -> `briefing` -> `running` -> `done|failed` (plus `verifying|rendering`).

1. On submit (`POST /api/requests` returns `status: intake`) the deep tier lists every human-necessary decision (`intake_discovery` prompt). Each is pre-filled via the preference store (request text > pinned > project > learned global, else the model's `proposed_default` with `source: model`). Decisions the request already settles are `locked`.
2. The run pauses as `awaiting_human` - always, even if everything is pre-resolved. `GET /api/runs/{id}/intake` returns `{status, decisions, answers, brief}`; each decision carries `prefill: {value, source, evidence}` and `locked`.
3. `POST /api/runs/{id}/intake` with `{"answers": {key: value}, "reasons": {key: str}, "accept_all": bool}`. `accept_all` fills every unanswered key with its prefill; locked keys are always filled. Unanswered `required` decisions give 400 listing the keys; unknown keys give 400.
4. Each answered key is recorded as a global-scope observation with this run's `run_id`/`project_id`: `source=request` if locked, `override` if the answer differs from a non-model prefill (with `reasons[key]`), otherwise `intake` (so accepting a model default or a learned default counts as `intake`).
5. The standard tier writes the build brief (`intake_brief` prompt); code appends an exact markdown decision table. It is stored in `runs.brief` and used as the root task's `context` and as binding "Project context" for the whole tree.

`POST /api/runs/{id}/resume` on `awaiting_human` is a 400; on `intake`/`briefing` (or a run that failed before it had a tree) intake restarts from scratch. SSE: the snapshot has `intake` per run, and `{"type":"intake","run_id","status"}` is published on each transition. Intake LLM escalations are logged in `task_log` under `task_id = "run:<run_id>"`.

## Plan

After the brief, the run enters a planning phase. Run states: `intake` -> `awaiting_human` -> `briefing` -> `planning` -> `awaiting_plan` -> `running` -> `done|failed`.

1. The deep tier (`plan_architect` prompt) turns the brief into an architecture (markdown), stack, repo layout, modules with explicit contracts (public interface, owned files, how tested), a DAG of atomic coding tasks (`id`, `module`, `tier`, optional `effort`, `depends_on` task ids, `acceptance` statements) and scaffold commands. Unknown/duplicate/cyclic references are dropped and logged as `plan_invalid` in `task_log` under `run:<run_id>`; a plan with no tasks fails the run.
2. The run pauses as `awaiting_plan`. `GET /api/runs/{id}/plan` returns `{status, plan, plan_meta, plan_revisions}`.
3. `POST /api/runs/{id}/plan` with `{"approve": bool, "note": str?, "plan": object?}`. `approve: false` needs a non-empty `note`; the plan is redone with the previous plan and a `REVISION NOTE` in the prompt (`plan_revisions` increments, notes are kept in `plan_meta.revisions`). `approve: true` may carry an edited `plan`, which is re-validated and replaces the stored one. Any status other than `awaiting_plan` gives 400.
4. Approval materialises the tree: an `integrator` root (`kind: llm`, context = brief + architecture + repo layout + all module contracts) with one child per plan task (`kind: code`, role `<module>-engineer`, prompt = task prompt + module contract, `tier`, `effort`, `module`, `acceptance`, `depends_on` resolved to sibling ids), then the normal scheduler runs it. Code tasks run in the sandbox (see Code tasks).

`resume` on `awaiting_plan` is a 400; on `planning` (or a run that failed after its brief but before a tree existed) planning restarts. SSE: snapshot has `plan` per run; `{"type":"plan","run_id","status"}` on each transition.

## Sandboxes

After plan approval each run owns one sandbox. Run states now include `provisioning`: `awaiting_plan` -> `provisioning` -> `running` -> `done|failed`. No coding agent runs in it yet (task 07c); this is lifecycle only.

- `pipez/pve.py` `ProxmoxClient` (scoped API token; VMs enumerated via `GET /pools/{pool}`; 5xx/transport errors retried 3 times then `PveTransientError`, 4xx is `PveError`). `pipez/sandbox.py`: `PveSandbox` linked-clones the template into pool `pipez` (`pipez-<run_id>`), injects `~/.ssh/pipez_ed25519.pub`, boots, finds the IPv4 via the guest agent, waits for SSH, creates `/work/<run_id>`. `run`/`put`/`get` use the `ssh` binary. Env keys ending `_KEY`/`SECRET`/`TOKEN` go to `/work/<run_id>/.env` (mode 600) and are sourced, never put on the command line. `MockSandbox` uses a local temp dir (used when `PIPEZ_MOCK=1` or `PIPEZ_SANDBOX=mock`).
- Teardown: destroyed when the run is `done`; kept (VM left running, `sandbox_kept` logged on `run:<id>` with vmid/ip) when it fails, so you can `ssh etl@<ip>`. `resume` reuses a kept sandbox if it is still running, otherwise provisions a new one. Table `sandboxes` statuses: `provisioning|ready|destroying|destroyed|kept|failed`.
- API: `GET /api/sandboxes`, `GET /api/runs/{id}/sandbox`, `POST /api/sandboxes/{run_id}/destroy` (kept/failed only). SSE: `{"type":"sandbox","run_id","sandbox"}` on each change; snapshot has `sandbox` per run. The UI run header shows `sandbox vmid · ip · status` with a destroy button when kept.
- Config: `PIPEZ_SANDBOX` (`pve|mock`), `PIPEZ_SANDBOX_BOOT_TIMEOUT` (240), `PIPEZ_DEPLOY_BOOT_TIMEOUT` (600, deploy VM guest-agent wait), `PIPEZ_SSH_KEY`, `PIPEZ_SANDBOX_KEEP_ON_FAILURE` (1), `PIPEZ_PVE_URL/TOKEN_ID/TOKEN_SECRET/NODE/POOL/TEMPLATE/VMID_RANGE/VERIFY_TLS`. The token secret is read only in `config.py`.

## Code tasks

A `kind == "code"` task never calls the decompose decision; it runs `infra/runner/pipez_runner.py` (Claude Agent SDK, `bypassPermissions`, the VM is disposable) in the run's sandbox.

- Workspace (`Engine._init_workspace`, right after provisioning): `<workdir>/repo` with `git init -b main`, identity `pipez`, each `scaffold_commands` entry run via `bash -lc` (logged as `scaffold` rows on `run:<id>`; a non-zero rc fails the run with `scaffold_failed` before any leaf), a `scaffold` commit, branch `integration` (checked out). `.pipez/`, `.venv/`, `node_modules/`, `__pycache__/` are in `.git/info/exclude`. The runner is uploaded to `<workdir>/runner.py`; `ANTHROPIC_API_KEY` goes to `<workdir>/.env` via the sandbox secret-env path. An existing repo with an `integration` branch is reused (resume).
- Leaf (`_execute_code_task`): under the run's merge lock `git worktree add <workdir>/wt/<task_id> -b task/<task_id> integration`, writes `.pipez/task.md` (brief, architecture, layout, module contract, prompt, acceptance checklist, dependency summaries, runner contract), runs the runner, which commits and writes `.pipez/result.json`. See `infra/runner/README.md` for the contracts.
- Policy: runner `ok=false`/`is_error` fails the task; `tests.failed > 0` fails it when `PIPEZ_LEAF_REQUIRE_TESTS=1`; otherwise `git merge --no-ff task/<id>` into `integration` under the lock. A conflict is aborted (`merge_conflict` logged with the files) and fails the task; dependents are skipped (06a). Success stores `{summary, output, facts, metadata{files_changed, commit, tests, cost_usd, num_turns}}`; tokens and `tasks.cost_usd` accumulate.
- Resume: a failed code task re-runs from a fresh worktree/branch off the current `integration`. A run that ends with any failed/skipped task keeps its sandbox so resume continues in the same repo.
- Mock mode runs `infra/runner/mock_runner.py` with real git in the mock sandbox dir.

## Delivery

After every code task has merged into `integration`, the integrator root (`Engine._integrate`, before its LLM synthesis) works in `<workdir>/repo` on `integration`:

1. `git status --porcelain` must be empty, else the run fails with "dirty working tree".
2. Runs the plan's `build_command` then `test_command` (`bash -lc`, each limited by `PIPEZ_INTEGRATE_TIMEOUT`), logging `integrate_build` / `integrate_test` (cmd, rc, seconds, tail). An empty command is skipped. The architect states both in the plan.
3. Pass: `git checkout main && git merge --ff-only integration`, tag `pipez/<run_id>`, then `repo-<run_id>.tar.gz` (`git archive main`) and `repo-<run_id>.bundle` (`git bundle create --all`) are fetched into `artifacts/<run_id>/` and registered, served at `/artifacts/<run_id>/<file>`; `integrate_ok` is logged. The root synthesis gets an "# Integration" section (commands, rc, tails, files changed, tag) and its output is the "how to run it" text shown in the UI.
4. Fail: `integrate_failed` is logged and the integration fix loop (below) runs; once the rounds are used up the root and run fail with the failing tail as the error, `main` is untouched, no tag or artifacts, and the sandbox is kept.

Runs with a plan never use LLM artifact rendering; the repo is the deliverable. If any task failed or was skipped, integration is not attempted (heal the gaps and resume). The UI shows cost, tests and file count per code task (commit in the tooltip), the two download links, and the failing tail in a collapsible block.

## Control and fix loops

- **Stop**: `POST /api/runs/{id}/stop` (UI: Stop button in the run header while the run is not `done|failed|cancelled`). The run's driver task is cancelled (running runner processes are killed), every `running|pending|awaiting_children|synthesizing|awaiting_human` task becomes `cancelled` (error "stopped by user"), the run becomes `cancelled`, and the sandbox is kept (`sandbox_kept` logged). Works in any phase; before the plan tree exists it just marks the run. `POST /resume` on a cancelled run re-executes the cancelled/pending tasks like failed ones (done tasks stay done); a run stopped at the plan gate returns to `awaiting_plan`.
- **Mid-run question**: a code task whose agent cannot proceed without a human choice writes `.pipez/question.json` instead of `result.json` (task.md says: only when the brief does not settle it, never for engineering decisions). The task becomes `awaiting_human` with `tasks.question` set, the run `awaiting_human`; sibling tasks keep running, dependents wait. UI: "Waiting on you" cards above the task table. `POST /api/runs/{id}/tasks/{task_id}/answer {value, reason?}` records an observation (key from the question, `task.` prefix when it has no dot; scope `request`, source `intake`), adds it to the run's `answers`, and re-runs the task in a fresh worktree with a "# Human answer" section in task.md; the run returns to `running` when no task waits. After a server restart a waiting task is re-run from the stored answer by the answer call (or `resume`). A task may ask at most 3 times.
- **Leaf retry**: `PIPEZ_LEAF_RETRIES` (1). A runner error, `ok=false`, failed tests, or (with `PIPEZ_LEAF_REQUIRE_TESTS=1`) `tests: null` logs `leaf_retry` with the tail, drops the worktree/branch and re-runs with a "# Previous attempt failed" section (last 2000 chars). Cost and tokens accumulate on the task. Merge conflicts are not retried.
- **Integration fix loop**: `PIPEZ_FIX_ROUNDS` (2). When build/test (or the clean-tree check) fails in `_integrate`, a child of the root is created (`code`, role `integrator-engineer`, module `*`, title "Fix integration (round n)", no dependencies) whose prompt holds the failing command and tail; it runs the leaf runner on a detached worktree at `integration` and its commit is fast-forwarded into `integration` (no merge commit). Then `_integrate` runs again. `integrate_fix` is logged on the root. A failed fixer or exhausted rounds fail the run as before.
- **Baseline `.gitignore`**: if the scaffold leaves no `.gitignore`, `__pycache__/ *.pyc .pytest_cache/ .mypy_cache/ .ruff_cache/ node_modules/ .venv/ dist/ build/ .pipez/` is written and committed with the scaffold.

## Deploy

When the plan has `deploy.target == "vm"` (the architect fills it when the brief chose VM hosting) and `PIPEZ_DEPLOY_AUTO=1`, a successful integration is followed by a deployment (run status `deploying`, then back to `done`). `POST /api/runs/{id}/deploy` does the same by hand for a `done` run with a tarball and no active deployment; `POST /api/runs/{id}/undeploy` destroys the VM.

- Plan `deploy`: `{target: vm|none, install_commands[], start_command, port, health_path, env[{name,value}]}`; `{port}` in `start_command` is substituted. Secrets are listed in `env` by name with an empty value; the operator fills `/opt/<app>/.env` on the VM.
- Steps (`pipez/deploy.py`, `Deployer`): clone a fresh VM from template 9000 via `PveSandbox` (VMID 9200-9299, name `pipez-app-<run_id>`, 2 cores / 2048 MB, pool `pipez`), upload the run's `repo-<id>.tar.gz`, extract to `/opt/<app>` (`<app>` = the top-level directory of the plan's `repo_layout`, else `app-<run_id>`), run each install command with `bash -lc` in that directory, write and start `/etc/systemd/system/<app>.service` (user `etl`, `Restart=on-failure`, `EnvironmentFile` when `env` is non-empty), poll `curl -sf http://<ip>:<port><health_path>` from the VM for up to `PIPEZ_DEPLOY_HEALTH_TIMEOUT`.
- State: table `deployments` (status `provisioning|installing|starting|ready|failed|destroyed`, `url`, `error`, `log`); `GET /api/runs/{id}/deployment`, `GET /api/deployments`; SSE `deployment` events and a snapshot entry. A failure marks the deployment `failed` (`deploy_failed` logged with the tail) and keeps the VM for inspection; the run stays `done`. Stop while `deploying` destroys the VM and marks the deployment `failed`; the run returns to `done`.
- Mock mode: the "VM" is a temp dir, the service a local background process (a free port is used if the plan's is busy), `sudo`/`apt` install commands are skipped, there is no systemd; undeploy kills it.
- UI: run header shows `Deployed: <url>`, the failure tail, and Deploy / Undeploy buttons; the plan panel shows the deploy target, port, health path and install-command count.

## Local tier
An optional `local` tier (OpenAI-compatible endpoint, e.g. vLLM serving qwen3.6) exists when
`PIPEZ_LOCAL_BASE_URL` and `PIPEZ_LOCAL_MODEL` are set. Measured throughput is about 9 output
tokens/s, so long outputs take minutes; `PIPEZ_LOCAL_TIMEOUT` bounds a call and a truncated
response reports its elapsed seconds and output tokens. Code tasks never run on `local` (a plan
task with `tier=local` is coerced to `light`, logged as `tier_coerced`). `PIPEZ_LOCAL_STAGES`
(comma list of `intake,brief,plan,decide,synthesize`, default empty) lists the stages that may
auto-select `local`; with it empty `local` is used only by an explicit task tier override and by
the benchmark. To decide where to allow it, replay stored runs through the tiers:

    python scripts/bench_local.py --db pipez.sqlite3 --runs all --stages intake,brief \
        --tiers local,light,standard --out bench --max-per-stage 2 [--dry-run] [--yes]

It needs `ANTHROPIC_API_KEY` and `PIPEZ_LOCAL_*` in the environment, prints the estimated spend
first and requires `--yes`; the source DB is copied, never modified.
