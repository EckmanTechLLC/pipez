# Task 25 — Deployer waits for cloud-init and apt locks before install commands

## Objective
Live finding (run aabf050e8aba, deploy VM 9200 cloned from the fast template 9002):
the guest agent now reports an IP ~30 s after boot, so the Deployer ran
`sudo apt-get update` while Ubuntu's first-boot apt jobs (cloud-init / apt-daily /
unattended-upgrades) still held `/var/lib/apt/lists/lock` → rc 100, deployment
failed. The Deployer must wait for the guest to be quiescent before running any
install command: `cloud-init status --wait` (bounded) and then wait until no process
holds the apt/dpkg locks (bounded), logging how long it waited.

## Reference (read first)
- `pipez/deploy.py` `Deployer.run` (~158–215): provision → extract → env →
  `install_commands` loop (mock skips apt commands) → unit → health; `_sh`, `note`,
  `emit`, `report`, `self.mock`.
- `pipez/sandbox.py` `PveSandbox.run(cmd, cwd, timeout)`; `MockDeploySandbox` /
  `MockSandbox.run` in `pipez/deploy.py` or `pipez/sandbox.py` (how mock commands are
  executed and whether unknown commands succeed).
- `pipez/config.py` — `DEPLOY_BOOT_TIMEOUT` (~191) for the env-var style.
- `tests/test_deploy_failure.py`, `tests/test_deploy_hygiene.py` — how a mock deploy
  is driven and events asserted (`deploy_install`).

## Deliverables
1. `config.DEPLOY_READY_TIMEOUT = float(env PIPEZ_DEPLOY_READY_TIMEOUT, default
   600)`; documented in `.env.example` and `README.md` next to
   `PIPEZ_DEPLOY_BOOT_TIMEOUT`.
2. `Deployer._wait_ready()` called in `run()` right after `provisioned …` and before
   the tarball extract (the extract uses sudo+tar only, but keep it simple: wait
   first). Non-mock: run on the guest
   `timeout <T> cloud-init status --wait >/dev/null 2>&1; echo cloud-init:$?` then a
   loop (single shell command, bounded by the remaining time) that sleeps 2 s while
   `sudo fuser /var/lib/apt/lists/lock /var/lib/dpkg/lock /var/lib/dpkg/lock-frontend
   >/dev/null 2>&1` succeeds or `pgrep -x apt-get|apt|dpkg|unattended-upgr` finds a
   process; finally `sudo systemctl is-active apt-daily.service
   apt-daily-upgrade.service` just for the note. Emit `deploy_wait_ready {"seconds":
   …, "cloud_init_rc": …, "timed_out": bool}` and `note("waited Ns for cloud-init /
   apt locks")`. On timeout: log and CONTINUE (do not fail; the install command
   itself will fail loudly if the lock is still held). Mock: emit the event with
   seconds 0 and skip. Never use `pkill`/`killall`.
3. Retry once: if an install command fails with rc 100 and its output contains
   "Could not get lock" or "Unable to lock", wait for the locks again (same helper)
   and re-run that command once; emit `deploy_install_retry`. Any other failure is
   unchanged.
4. Tests (`tests/test_deploy_wait.py`): (a) mock deploy emits `deploy_wait_ready`
   before the first `deploy_install`; (b) the lock-retry path: with a mock sandbox
   whose first run of a given command returns rc 100 + "Could not get lock" and the
   second rc 0, the deployment becomes `ready` and `deploy_install_retry` is emitted;
   (c) a non-lock failure still fails without retry. Full suite `python -m pytest -q`
   (use `.venv/bin/python`; ~5–7 min; timeout ≥ 560 000 ms or nohup + poll; do not
   assume a hang before 8 minutes; rerun flaky failures).

## Out of scope
- Template changes (disabling apt-daily in 9002 is a separate decision); build
  sandbox (91xx) scaffold; any engine/planner/UI change.
- Never run `pkill`/`killall`; never touch ports 8400/8401; never touch Proxmox.

## Verification criteria
- `grep -n "_wait_ready\|deploy_wait_ready\|deploy_install_retry\|DEPLOY_READY_TIMEOUT" pipez/deploy.py pipez/config.py` non-empty.
- `python -m pytest -q` green; new test file has ≥ 3 tests.

## Write summary
`.odin/sessions/task-25-deploy-wait-ready-summary.md`: files changed, test count,
anything deferred.
