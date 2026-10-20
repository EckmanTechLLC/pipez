# Task 22 — Child runs of a plan run start from the parent's repository bundle

## Objective
Live finding (run da31df2bfc2a, child of c7213225e46a): a request submitted with
`parent_run_id` gets a fresh sandbox and an EMPTY `git init` repo, while the planner —
told that the prior run's output is the baseline — emitted
`git checkout -b systemd-deploy pipez/c7213225e46a` and the scaffold failed. For a
parent that was a plan run, the child's workspace must be seeded from the parent's
`repo-<parent>.bundle` artifact (produced by `_integrate`), so `main`, the
`pipez/<parent>` tag and the full tree exist before the plan's scaffold commands run,
and the planner must be told exactly that (repo present at `main`, tree listing),
so it plans an increment instead of re-scaffolding.

## Reference (read first)
- `pipez/server.py` `submit` (~91–118): how the parent context ("Prior deliverable",
  "Prior facts") is built; `pipez/engine.py` `submit` (~203).
- `pipez/engine.py` `_init_workspace` (~1807–1862): the `git init` scaffold steps, the
  `workspace_reused` branch, `scaffold_commands` loop, `integration` branch.
- `pipez/engine.py` `_integrate` (~2532–2610): artifact names `repo-<rid>.bundle` and
  the tar.gz, `config.ARTIFACTS_DIR/<rid>/`, `db.add_artifact`/`get_artifacts`.
- `pipez/engine.py` plan prompt builder (grep `_plan_prompt` / `llm.plan(` ~501) and
  `_brief_prompt` (~379): where the parent context reaches the planner.
- `pipez/sandbox.py` `Sandbox.put`, `run` (cwd, timeout); `MockSandbox` equivalents.
- `pipez/db.py` `get_artifacts`, `get_run` (`parent_run_id`, `result`).
- `tests/test_code_leaf.py`, `tests/test_control.py` (resume) — fixtures that drive a
  plan run through `_init_workspace` with the mock sandbox.

## Deliverables
1. `_init_workspace`: if `run["parent_run_id"]` is set and
   `ARTIFACTS_DIR/<parent>/repo-<parent>.bundle` exists → upload it with `sb.put` to
   `<wd>/parent.bundle`, then run `git clone -q <wd>/parent.bundle <repo>` (instead of
   `rm -rf repo && git init`), `git checkout -q main` (fall back to the bundle's HEAD
   branch if `main` is absent), the same `git config user.*` and `.git/info/exclude`
   lines, then the plan's `scaffold_commands`, `.gitignore` baseline check, `git add -A
   && git commit -q --allow-empty -m "scaffold (child of <parent>)"`, `git branch
   integration`, `git checkout -q integration`. Log `workspace_seeded
   {"parent": ..., "bundle": ..., "head": <sha>}`. If the bundle is missing, log
   `workspace_seed_missing` and fall back to today's `git init` path.
2. Planner context: when the parent is a plan run with a bundle, the plan prompt must
   contain a section `# Existing repository` stating: the repo is already present at
   branch `main` (tag `pipez/<parent>`) with the parent's `repo_layout` (from the
   parent's plan) and `stack`; "do NOT re-scaffold, do not `git init`/`npm init`/
   `git checkout` tags; scaffold_commands may only install dependencies (e.g. `npm
   ci`) and create new directories"; and the parent's `build_command`/`test_command`.
   Put the builder in `_plan_prompt` (or wherever the plan prompt is assembled) so the
   benchmark/tests can reuse it. The `server.submit` "Prior deliverable" text stays.
3. `_validate_plan`/`_materialise_plan` or the scaffold loop: for a seeded child run,
   reject scaffold commands matching `^\s*git (init|checkout|clone)\b` or `npm init`
   with `ScaffoldError("scaffold_failed: '<cmd>' is not allowed on a seeded
   workspace")` logged BEFORE running anything (so the sandbox is not wasted on a
   doomed plan). Log `scaffold_filtered`.
4. Tests (`tests/test_child_run.py`): (a) parent plan run with the mock sandbox
   produces a bundle artifact; a child run's `_init_workspace` seeds from it (assert
   the clone/checkout commands were issued on the mock sandbox and `workspace_seeded`
   is logged); (b) missing bundle → `workspace_seed_missing` + git-init path; (c) the
   plan prompt of a child contains `# Existing repository` and the parent's tag;
   (d) a `git checkout` scaffold command on a seeded child raises before any scaffold
   command runs. Also fix the flaky `tests/test_sandbox.py::
   test_secret_env_goes_to_dotenv_not_command`: use a secret value that cannot occur
   in a temp path (e.g. `"s3cr3t-VALUE-9f8e7d"`) instead of `t1`.
5. Full suite `python -m pytest -q` passes (~5 min; timeout ≥ 560 000 ms or nohup +
   poll; do not assume a hang before 8 minutes; rerun flaky failures).

## Out of scope
- Changing how non-plan (deliverable) child runs work; UI changes; runner changes.
- Never run `pkill`/`killall`; never touch ports 8400/8401; never touch Proxmox.

## Verification criteria
- `grep -n "workspace_seeded\|workspace_seed_missing\|scaffold_filtered\|Existing repository" pipez/engine.py` all present.
- `python -m pytest -q` green; new test file has ≥ 4 tests; the sandbox test no longer
  uses a 2-character secret.

## Write summary
`.odin/sessions/task-22-child-run-workspace-summary.md`: files changed, test count,
anything deferred.
