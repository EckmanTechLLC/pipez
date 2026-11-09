# Task 30 — Bound local-tier calls: per-tier max_tokens, no retry after timeout/truncation

## Objective

`OpenAICompatProvider` sends `max_tokens: config.MAX_TOKENS` (128000) to every local
model. On a 65k-context reasoning model this let one call generate ~52k tokens of
reasoning over 30 minutes; the client timed out (`PIPEZ_LOCAL_TIMEOUT`), the facade
retried three times with backoff, and each retry queued another 30-minute runaway
behind the first (verified in the inference server's logs: five back-to-back ~55k-token
tasks). Give each local tier its own `max_tokens`, and make a local call that times out
or is truncated at `max_tokens` fail immediately — no facade retry — so the engine's
normal tier escalation takes over instead of stacking identical calls.

## Reference (read first)

- `pipez/config.py`: the local-tier block (`LOCAL_*`, `LOCAL_REASON_*`, the two
  `MODEL_TIERS[...]` dicts, `LOCAL_TIERS`, `is_local`), `MAX_TOKENS` (~166),
  `TASK_RETRIES`/`RETRY_BASE_DELAY` (~170–172).
- `pipez/providers.py`: `LLMError`, `ProviderTransientError`, `is_transient` (~25–35);
  `OpenAICompatProvider.complete` (~212–262): `g(tier, key, default)` helper, the body
  dict (`"max_tokens": config.MAX_TOKENS` ~227), the `httpx.HTTPError →
  ProviderTransientError` mapping (~241), the `finish_reason == "length"` →
  `LLMError("response truncated at max_tokens …")` branch (~252).
- `pipez/llm.py` `LLM._call` (~298–320): retries on `is_transient(e)` and on any
  `LLMError` whose text contains "truncated".
- `pipez/engine.py` `_llm_call` (~111–143): escalation on `LLMError` via
  `config.next_tier_up` — unchanged by this task, but the new errors must still be
  `LLMError` subclasses so escalation happens.
- Tests: `tests/test_providers.py` (MockTransport patterns), `tests/test_local_tiers.py`
  (config reload pattern), `tests/test_escalation.py`.
- `.env.example`, `README.md` ("Local tiers" section and env table).
- `.odin/sessions/task-29-local-tiers-summary.md` for what task 29 changed.

## Deliverables

1. **Config**: `PIPEZ_LOCAL_MAX_TOKENS` (default `16000`) and
   `PIPEZ_LOCAL_REASON_MAX_TOKENS` (falls back to the `local` value). Each local tier
   dict carries `max_tokens`. Anthropic tiers are untouched (`config.MAX_TOKENS` stays
   their cap).
2. **Provider**: `OpenAICompatProvider.complete` sends `g(tier, "max_tokens",
   config.MAX_TOKENS)`. Introduce `ProviderTimeoutError(LLMError)` and
   `ProviderTruncatedError(LLMError)` in `providers.py`, both with class attribute
   `retryable = False`. Map `httpx.TimeoutException` (a subclass of `httpx.HTTPError` —
   check it *before* the generic branch) to `ProviderTimeoutError` with a message
   naming the tier's model, base_url and the timeout in seconds; other
   `httpx.HTTPError`s stay `ProviderTransientError`. The `finish_reason == "length"`
   branch raises `ProviderTruncatedError` (keep the existing message text, add the
   `max_tokens` used).
3. **Facade**: `LLM._call` does not retry an exception whose `retryable` attribute is
   `False` — it re-raises immediately. Everything else keeps today's behaviour
   (transient → retry; the Anthropic provider's truncation `LLMError` → retry).
4. **Logging**: when `_call` re-raises a non-retryable error, log one `warning` line
   `"not retrying: %s"`; the engine's existing `escalated` log then records the move.
5. **Tests** (`tests/test_local_bounds.py`, new):
   - config reload with `PIPEZ_LOCAL_MAX_TOKENS=1234` and no REASON override → both
     local tiers have `max_tokens == 1234`; with `PIPEZ_LOCAL_REASON_MAX_TOKENS=777`
     → `local_reason` is 777, `local` stays 1234; unset → 16000.
   - provider via `httpx.MockTransport`: the request body's `max_tokens` equals the
     tier's value; a transport that raises `httpx.ReadTimeout` yields
     `ProviderTimeoutError` (and `is_transient` is False for it); a response with
     `finish_reason: "length"` yields `ProviderTruncatedError`.
   - facade: with a stub provider raising `ProviderTimeoutError`, `LLM._call` makes
     exactly one attempt (no sleep — patch `asyncio.sleep` or assert call count on
     the stub) and re-raises; with `ProviderTransientError` it still retries
     `TASK_RETRIES` times (patch `config.RETRY_BASE_DELAY` to 0).
   - engine: with stub providers, a `local_reason` task whose call raises
     `ProviderTimeoutError` escalates once to `light` and succeeds (one `escalated`
     log row with the timeout message), total local attempts == 1.
6. **Docs**: `.env.example` gains the two vars; `README.md` env table + "Local tiers"
   section state the defaults, that local timeouts/truncations are not retried but
   escalate, and why (reasoning models fill their context when uncapped).

## Out of scope

- No change to Anthropic-tier retry/truncation handling, `MAX_TOKENS`, or
  `TASK_RETRIES` semantics for transient errors.
- No prompt changes, no engine changes beyond what escalation already does.
- Do not edit `.env`; do not commit; do not deploy; never `pkill`; never touch ports
  8400/8401, any VM or the Proxmox host; tests must not reach the network.

## Verification criteria

- `.venv/bin/pytest -q tests/test_local_bounds.py tests/test_providers.py tests/test_local_tiers.py tests/test_escalation.py` passes.
- Full `.venv/bin/pytest -q` passes (the known timing-flaky
  `tests/test_resilience.py::test_stop_kills_remote_runner_group` may be rerun alone).
- `grep -n "max_tokens" pipez/providers.py` shows the OpenAI-compatible body using the
  tier value, and the Anthropic path unchanged.
- `.venv/bin/python -c "from pipez import config; print({k:v.get('max_tokens') for k,v in config.MODEL_TIERS.items()})"`
  prints a value for each local tier and `None` for the Anthropic tiers.

## Write summary

Last step, mandatory: `.odin/sessions/task-30-local-call-bounds-summary.md` — files
changed, new env vars, test counts before/after, anything left undone or uncertain.
