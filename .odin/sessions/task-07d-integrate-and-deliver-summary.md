# Task 07d summary: integrate and deliver

## Changed
- pipez/engine.py: `_integrate` (clean-tree check, `bash -lc` build then test with `PIPEZ_INTEGRATE_TIMEOUT`, logs integrate_build/test/ok/failed, `checkout main` + `merge --ff-only integration`, tag `pipez/<run>`, `git archive` tar.gz + `git bundle --all`, `sb.get` into ARTIFACTS_DIR/<run>/ + `db.add_artifact`); `_integration_section` + `_synthesize(..., integration)`; wired into `_resume_task` for the root before synthesis; `_run_resume` skips `_render_artifacts` when the run has a plan; `_validate_plan` normalises build/test commands; `.pytest_cache/` added to the workspace git exclude.
- pipez/llm.py: `plan_schema()` optional `build_command`/`test_command`; MockLLM.plan returns `true` / `python3 -m pytest -q`.
- pipez/seed_prompts.py: plan_architect sentence. pipez/config.py: `INTEGRATE_TIMEOUT`.
- pipez/server.py: artifact route now also answers HEAD (needed for `curl -I` -> 200).
- ui/index.html: per-code-row cost / tests passed-failed / files (commit in tooltip), collapsible integration-failure block in the run header, "Run it" section from `result.output` for done code runs. Download links already covered tar.gz/bundle.
- tests/test_integrate.py (5 tests); README config row + "Delivery"; .odin/project-index.md.
- Plan values are stored on the run via the existing `runs.plan` JSON (no new columns).

## Verification
1. pytest tests/: 80 passed. Met.
2. Mock smoke on 8401 (temp DB/artifacts): submit, accept_all, answered open question, approve -> done; /artifacts lists repo-<id>.bundle and repo-<id>.tar.gz; `curl -sI` tar.gz -> 200 (after the HEAD fix; first try was 405); `tar tzf` lists api/t3.py, core/t1.py, core/t2.py and tests. 8401 stopped via fuser, temp dir removed, 8400 still listening and untouched. Met.
3. grep integrate_ok / integrate_failed / ff-only in engine.py: present. Met.
4. node --check on the extracted inline script: ok. Met.

## Decisions / interpretations
- Integration runs only when no child failed/skipped; otherwise it is skipped silently and the old behaviour (run done with gaps, sandbox kept) stays. Spec was silent.
- After a pass or fail, `integration` is checked out again so resume/new leaf merges keep working (leaf merges happen in the repo's checked-out branch).
- Dirty check runs before build and again after test (build outputs must be ignored/committed-free); both report "dirty working tree".
- Empty build/test command is skipped. Failure message (run.error and root error) is "integration failed: test `cmd` exited N:" followed by the tail; the UI keys on that prefix.
- Failing-tests test uses a plan with `test_command` "echo ...; false" (PIPEZ_MOCK_RUNNER_FAIL fails the leaf first, so it cannot reach integration).
- Existing tests unchanged; test_code_leaf happy paths now also run integration.

## Not done
- Fix loops, pushes, live Proxmox/LLM, git commit. pipez_runner.py untouched. UI not exercised in a browser.
