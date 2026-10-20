# Task 22 summary

## Changed
- pipez/engine.py: new `_seed_bundle(run)` (parent's `repo-<parent>.bundle` in ARTIFACTS_DIR or None), `_existing_repo_section(run)` (the `# Existing repository` prompt section), `_run_steps` helper. `_plan` appends the section to the plan prompt. `_init_workspace` now clones the bundle for seeded children (put to `<wd>/parent.bundle`, clone, checkout main, git config, exclude, scaffold commands, gitignore check, commit "scaffold (child of <parent>)", `checkout -B integration`), logs `workspace_seeded` {parent, bundle, head}; missing bundle logs `workspace_seed_missing` and uses the git-init path. Seeded scaffold commands matching git init/checkout/clone or npm init are rejected with ScaffoldError before anything runs, logged `scaffold_filtered`.
- tests/test_child_run.py (new, 4 tests): seed from bundle, missing bundle fallback, plan prompt section, rejected checkout scaffold.
- tests/test_sandbox.py: secret test uses distinctive values (`s3cr3t-VALUE-9f8e7d` etc.) instead of 2-char ones.

## Decisions
- Existing-repo section gives the parent's stack, repo_layout, build/test commands; no separate tree listing (repo_layout used as the task's deliverable 2 specifies).
- Used `git checkout -q -B integration` on the seeded path (clone may already have a local `integration` branch since the bundle HEAD is integration); `git checkout main || true` covers the missing-main fallback by staying on the cloned HEAD.
- Scaffold filter regex also catches the commands after `&&`, `;`, `||`.
- The filter is applied after the workspace-reuse check (resume), since nothing scaffolds on reuse.

## Verification
- grep for workspace_seeded / workspace_seed_missing / scaffold_filtered / Existing repository in engine.py: met.
- Full suite: 166 passed (308s). New file has 4 tests; sandbox test no longer uses a 2-char secret: met.

## Left alone
- server.submit "Prior deliverable" text, non-plan child runs, UI, runner. Tasks 20-21 working-tree changes untouched.
