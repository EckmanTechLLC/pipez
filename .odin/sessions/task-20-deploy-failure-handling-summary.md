# Task 20 summary

Changed
- pipez/config.py: `DEPLOY_BOOT_TIMEOUT` (env PIPEZ_DEPLOY_BOOT_TIMEOUT, default 600).
- pipez/deploy.py: `make_deploy_sandbox` passes `boot_timeout=config.DEPLOY_BOOT_TIMEOUT`.
- pipez/engine.py: new `_review_error`; `_run_planned` calls it when a vm deploy ended not-ready and VERIFY_AUTO is on (run done, verification error, sandbox kept via existing `_finish_review` rule); VERIFY_AUTO off still gives `skipped`. `reverify` raises the "kept but not attached (server restarted)" ValueError when the handle is missing. `_review` unchanged (needed no change).
- ui/index.html: "review error" badge now has a title (deployment failure text when the deployment failed). Deploy block unchanged (already shows failure text plus Deploy button).
- .env.example, README.md: documented PIPEZ_DEPLOY_BOOT_TIMEOUT (single-line edits only).
- tests/test_deploy_failure.py: 5 tests (flagged+kept+event; VERIFY_AUTO off -> skipped; manual deploy then reverify -> passed; unattached sandbox refused; deploy boot timeout).

Decisions
- The UI has no run-log feed, so the tooltip uses the deployment's error rather than the logged event text.
- Failure in tests simulated with an install command that fails until a marker file exists.

Verification
- grep DEPLOY_BOOT_TIMEOUT in config.py/deploy.py: met.
- grep _review_error in engine.py: definition + call: met.
- Full suite (.venv python): 162 passed; new file has 5 tests: met.

Deferred: nothing. Note: system `python` lacks httpx2; used .venv/bin/python.
