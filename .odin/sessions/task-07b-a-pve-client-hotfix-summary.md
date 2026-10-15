# Summary: task-07b-a PVE client hotfix

## Changes
- pipez/pve.py: `set_config` URL-encodes `sshkeys` once before the normal form encoding (matches smoke script: `%2520` for spaces). New `_err_text` puts Proxmox `message` + `errors` into `PveError` for 4xx/5xx instead of just the reason phrase. `wait_task` already raised on non-"OK" exitstatus (text included); only a test was added.
- pipez/sandbox.py: `_destroy_quietly` checks `status()` and pool membership first; if missing/not in pool it logs `sandbox_cleanup_skipped` and returns (no masking second error). `teardown` logs `sandbox_teardown_warning` and returns on 403/404 during destroy.
- Extra fix found by live check (not in task text, needed to pass it): /work does not exist in the template and etl cannot create it, so provision now runs `sudo mkdir -p <workdir> && sudo chown etl:etl <workdir>` (etl has passwordless sudo).
- Tests: tests/test_pve.py (double-encoded sshkeys, error body surfaced, wait_task failed exitstatus; replaced the old single-encoding test that asserted the buggy behavior), tests/test_sandbox.py (cleanup skips when VM gone, teardown tolerates 403, updated provision ssh expectation, FakeClient gained status/pool tracking).

## Verification
1. pytest tests/: 66 passed (57 + new). Met.
2. Live check (run_id=livecheck): met. Output trimmed:
   - vmid 9100 ip 192.168.50.59 clone->ssh-ready 31.6 s
   - `cat /etc/pipez-builder-version && claude --version`: rc 0, "built: 2026-10-14T16:02:48Z", "claude: 2.1.197 (Claude Code)" (plus node 20.20.2, python 3.12.15, docker 29.9.0), then "2.1.197 (Claude Code)"
   - put/get round trip under /work/livecheck/: b'hello-roundtrip'
   - teardown(False) ok; pool_members("pipez") = []; `qm list` on host shows no 91xx VM.
- First live attempt failed at mkdir (above); its cleanup destroyed the VM and teardown logged the 403 warning rather than raising. Two probe VMs (one diagnostic, 9100) were also created and destroyed via the token; root was used only read-only for `qm list`.

## Left alone
infra/, engine/UI, port 8400, VMs 9000/9001. No commit.
