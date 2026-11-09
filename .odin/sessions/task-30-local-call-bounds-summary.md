# Task 30 summary: bounded local calls

## Files changed
- pipez/config.py: LOCAL_MAX_TOKENS (default 16000), LOCAL_REASON_MAX_TOKENS (falls back to local); `max_tokens` in both local tier dicts. Anthropic tiers untouched.
- pipez/providers.py: ProviderTimeoutError and ProviderTruncatedError (LLMError subclasses, `retryable = False`); OpenAICompat body uses `g(tier, "max_tokens", config.MAX_TOKENS)`; httpx.TimeoutException caught before HTTPError (message names model, base_url, timeout seconds); length branch raises ProviderTruncatedError with the max_tokens used.
- pipez/llm.py: `_call` re-raises any exception with `retryable is False` immediately, logging warning "not retrying: %s". Transient and Anthropic-truncation behaviour unchanged.
- tests/test_local_bounds.py (new, 9 tests): config, provider body/timeout/transport error/truncation, facade (timeout, truncation, transient retries), engine escalation local_reason -> light with 1 local attempt and one `escalated` row.
- tests/test_escalation.py: one line; the test now pops `local_reason` from its tier copy.
- .env.example, README.md (env table + "Local tiers" section).

## New env vars
PIPEZ_LOCAL_MAX_TOKENS (16000), PIPEZ_LOCAL_REASON_MAX_TOKENS (defaults to the local value).

## Decisions / interpretation
- test_escalation.py edit was outside the listed deliverables but required: `test_local_failure_escalates_to_light` fails (also on the unmodified tree) whenever `.env` configures PIPEZ_LOCAL_REASON_*, since escalation then goes local -> local_reason -> light (2 rows). Made it env-independent by removing local_reason from the test's tier dict.
- Non-retryable check is `getattr(e, "retryable", True) is False`, so other exceptions keep current behaviour.

## Verification
- Four targeted test files: 29 passed. Met.
- grep max_tokens providers.py: OpenAI-compat body uses the tier value; Anthropic lines (113, 145, 160) unchanged. Met.
- Tier print: {'deep': None, 'standard': None, 'light': None, 'local': 16000, 'local_reason': 16000}. Met (this env's .env defines both local tiers).
- Full pytest -q: 212 passed (371s). Met.

## Test counts
Before: 203 (task 29). After: 212 (9 new).

## Left alone
.env, commits, deploys, engine code. Stale `elapsed`/started handling untouched.
