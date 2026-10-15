# Task 02a summary

- pipez/providers.py `AnthropicProvider._create`: on BadRequestError, check lowercased `e.message` + `str(e)` for "fallback" (also covers "server-side-fallback"); if absent, re-raise; otherwise warn, set `_fallbacks_ok = False`, retry without fallbacks.
- tests/test_providers.py: two async tests with stub client (fallbacks 400 disables and falls through to plain call; schema 400 propagates, `_fallbacks_ok` stays True).

Verification: `.venv/bin/python -m pytest -q tests/` -> 57 passed (met).
Left alone: everything else; port 8400, infra/, git untouched.
