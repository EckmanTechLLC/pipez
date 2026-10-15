# Task 07d: Integrator build/test, repo artifact, code-task UI

## Objective
Close the loop on a code run. After every leaf has merged into `integration`,
the integrator root runs the plan's build and test commands in the sandbox,
feeds the output into its synthesis, and — only if they pass — fast-forwards
`main`, tags the run, exports the repository as a downloadable artifact, and
marks the run `done`. Failing build/tests fail the run (sandbox kept; the fix
loop is task 08). The UI shows code-task details and the artifact.

## Reference (read first)
- `.odin/project-index.md`; summaries for 06b (plan shape, `_materialise_plan`, integrator root), 07c (workspace layout `/work/<run>/repo`, `integration` branch, merge lock, result/metadata shape, `Sandbox.git`), 07b/07b-a (`Sandbox.run/get`, teardown policy, artifacts dir), 04/05 (UI structure; brief view; task table)
- `pipez/engine.py`: `_resume_task`/`_decompose` synthesis path for the root; `_render_artifacts` (existing LLM-based artifact rendering — code runs must NOT use it; see below); `db.add_artifact`, `/artifacts/{run_id}/{filename}` route in `server.py`
- `pipez/llm.py`: `plan_schema()` — add two optional string fields

## Design (binding)
- **Plan schema**: optional `build_command` and `test_command` (strings, may be empty). `plan_architect` prompt: "state the single command that builds and the single command that runs all tests from the repo root, using only the scaffolded toolchain". `MockLLM.plan` returns `build_command: "true"`, `test_command: "python3 -m pytest -q"` (the mock runner creates real pytest files; tests must actually pass in the mock workspace).
- **Integrator step** (`engine._integrate(run, root)`; runs after all children finish, before the root's LLM synthesis, only for runs with a sandbox + plan): in `/work/<run>/repo` on `integration`: `git status --porcelain` must be empty; run `build_command` then `test_command` with `bash -lc`, each with timeout `PIPEZ_INTEGRATE_TIMEOUT` (1800 s), logging `integrate_build` / `integrate_test` rows (`cmd, rc, seconds, tail`). Pass → `git checkout main && git merge --ff-only integration && git tag pipez/<run_id>`; export: `git archive --format=tar.gz -o /work/<run>/repo-<run_id>.tar.gz main` plus `git bundle create /work/<run>/repo-<run_id>.bundle --all`; `get` both into `config.ARTIFACTS_DIR/<run_id>/` and `db.add_artifact` each; log `integrate_ok`. Fail (non-zero rc) → root task `failed` with the test tail as error, run `failed`, sandbox kept (07b policy); log `integrate_failed`.
- **Root synthesis prompt** for code runs gets a "# Integration" section: build/test commands, rc, tails, files changed count, tag; the synthesis must describe what was built and how to run it (that becomes `runs.result.output`). Keep `facts` from children.
- **No LLM artifact rendering for code runs**: skip `_render_artifacts` when the run has a plan (the repo IS the deliverable); keep it for plain document runs.
- **UI**: task table rows for `kind == "code"` show `cost`, `tests passed/failed`, `files` (count) from `metadata`, and a tooltip with `commit`; run header shows artifact links (already exists for files — make sure `.tar.gz`/`.bundle` appear) and, when integration failed, the test tail in a collapsible block; `done` code runs show "run it" instructions from `result.output` under the brief view.
- **Tests** (`tests/test_integrate.py`, mock sandbox + real git + the mock runner): happy path → `integrate_ok`, `main` tag `pipez/<run_id>` exists, two artifacts registered and present on disk, run `done`, `_render_artifacts` NOT called (spy); failing `test_command` (use `PIPEZ_MOCK_RUNNER_FAIL` or a plan whose test command is `false`) → run `failed`, `integrate_failed` logged, sandbox row `kept`, no tag, no artifacts; dirty `integration` (inject a stray file) → `integrate_failed` with "dirty working tree".

## Deliverables
1. `plan_schema()` fields + prompt sentence + `MockLLM.plan` values; `_materialise_plan` stores them on the run (`runs.plan` already JSON — no new columns).
2. `engine._integrate` + wiring into the root's completion path; skip LLM rendering for code runs; config `PIPEZ_INTEGRATE_TIMEOUT`.
3. Artifact export via `get` into `ARTIFACTS_DIR/<run_id>/` + `db.add_artifact`; served by the existing route.
4. UI changes above; `node --check` passes.
5. `tests/test_integrate.py`; README "Delivery" section; `.odin/project-index.md`.

## Out of scope
- Fix loops on failed integration, mid-run human pauses (08). Deploy to a VM, pushing to a git remote (09/10). Live Proxmox/LLM runs (controller). Committing to git.

## Verification criteria
1. `.venv/bin/python -m pytest -q tests/` passes.
2. Mock smoke on **8401**: submit → accept_all → answer open question → approve → `done`; `GET /api/runs/{id}/artifacts` lists `repo-<id>.tar.gz` and `repo-<id>.bundle`; `curl -sI localhost:8401/artifacts/<id>/repo-<id>.tar.gz` → 200; `tar tzf` of the downloaded file lists the mock module files. Stop 8401; clean up.
3. `grep -n "integrate_ok\|integrate_failed\|ff-only" pipez/engine.py` present.

## Write summary
`.odin/sessions/task-07d-integrate-and-deliver-summary.md`: files, verification output, ambiguities resolved.
