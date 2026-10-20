# Task 25 summary

Changed
- pipez/config.py: DEPLOY_READY_TIMEOUT (env PIPEZ_DEPLOY_READY_TIMEOUT, default 600).
- .env.example, README.md: documented next to PIPEZ_DEPLOY_BOOT_TIMEOUT.
- pipez/deploy.py: Deployer._wait_ready(cloud_init=True) called in run() right after "provisioned" and before the tarball extract. Non-mock: bounded `cloud-init status --wait`, then a single bounded shell loop (sudo fuser on the three lock files / pgrep -x) sleeping 2 s, then systemctl is-active for the note. Emits deploy_wait_ready {seconds, cloud_init_rc, timed_out} and a note; timeout logs and continues. Mock: emits seconds 0 and skips.
- pipez/deploy.py: install loop retries once on rc 100 with "Could not get lock"/"Unable to lock" in output: emits deploy_install_retry, re-waits for locks (skips cloud-init), re-runs the command. Other failures unchanged.
- tests/test_deploy_wait.py: 3 tests (wait event before first install; lock retry then ready; non-lock failure not retried).

Decisions
- The retry wait does not re-run cloud-init status (only the lock wait), and also emits deploy_wait_ready (so two events in the retry test).
- The non-mock shell paths cannot be exercised without a VM; they were not run live.

Verification
- grep of the four names in deploy.py/config.py: non-empty, met.
- New test file: 3 tests, pass, met.
- Full suite (.venv/bin/python -m pytest -q): 179 passed, 1 failed. NOT fully green: tests/test_local_bench.py::test_plan_revision_config_defaults fails (PLAN_REVISION_TIER is 'standard', expected 'deep'). It fails identically with my changes stashed, so it is pre-existing and unrelated (likely local .env/config); left alone.

Left alone: templates, build sandbox, engine/planner/UI. No commit.

Follow-up (test_local_bench env independence)
- tests/test_local_bench.py: test_plan_revision_config_defaults and test_intake_tier_default_and_validation now compare against the env-derived values (valid_tier(os.environ...), PLAN_EFFORT fallback) instead of literals; added `import os`. No other test asserts literal defaults for these (tests/test_plan.py compares to config.PLAN_EFFORT already).
- Full suite now green: 180 passed. The earlier "not fully green" note above is resolved.

Follow-up 2 (always-timeout fix)
- pipez/deploy.py: removed unattended-upgr from the pgrep pattern (now apt-get|apt|dpkg); the permanent unattended-upgrade-shutdown daemon no longer blocks the wait. fuser on dpkg lock-frontend still covers real unattended runs. cloud-init rc 2 was never treated as a timeout (only rc 124 or the lock-loop sentinel); now covered by a test.
- tests/test_deploy_wait.py: new test with a fake non-mock sandbox asserting no "unattended" in any shell command and rc 2 -> timed_out False. 4 tests pass.
- Full suite: 185 passed (includes task-26 changes in the tree, untouched).
