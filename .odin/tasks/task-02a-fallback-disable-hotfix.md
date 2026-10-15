# Task 02a (hotfix): only disable server-side fallbacks when the API rejects fallbacks

## Objective
`AnthropicProvider._create` catches every `BadRequestError` on the fallbacks
path and permanently disables fallbacks for the process. A live run showed it
tripping on an unrelated schema error. Narrow it: disable only when the 400
message mentions fallbacks/the beta; otherwise re-raise so the real error is
reported once (not retried without fallbacks).

## Reference
- `pipez/providers.py` `AnthropicProvider._create` (the `except self._anthropic.BadRequestError` block); `tests/test_providers.py` for the test style (the Anthropic path may be tested with a stub client object exposing `beta.messages.stream` / `messages.stream` context managers).

## Deliverables
1. In `_create`: on `BadRequestError`, inspect `e.message` (and `str(e)`); if it contains `fallback` (case-insensitive) or `server-side-fallback`, log the warning, set `_fallbacks_ok = False`, and retry without fallbacks as today; otherwise `raise`.
2. `tests/test_providers.py`: two tests with a stub client — a 400 mentioning "fallbacks" disables and falls through to the non-beta call; a 400 about a schema error propagates unchanged and `_fallbacks_ok` stays True.

## Out of scope
Everything else. Do not touch port 8400, infra/, git.

## Verification criteria
`.venv/bin/python -m pytest -q tests/` passes (55 prior + 2).

## Write summary
`.odin/sessions/task-02a-fallback-disable-hotfix-summary.md` (short).
