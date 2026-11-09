# Task 29 — Two local tiers: `local` (coder) and `local_reason` (reasoner)

## Objective

pipez has one OpenAI-compatible tier, `local`, configured by global `PIPEZ_LOCAL_*`
settings. The local inference host now serves two models on different ports: a fast
coder (qwen3.6-coder, Qwen chat template, `chat_template_kwargs.enable_thinking`)
and a deeper reasoner (gpt-oss-120b, honours `reasoning_effort`, tolerates
`chat_template_kwargs`). Add a second tier, `local_reason`, with its own endpoint,
model, key, sampling and thinking settings, so that decomposition-type stages can
run on the reasoner while `local` stays the coder. Everything that treats `local`
specially (existence check, health probe, escalation order, stage gating, code-task
coercion, pricing, bench env check) must treat both local tiers the same way.
Prompts do not change.

## Reference (read first)

- `pipez/config.py` lines 19–112: `MODEL_TIERS`, `LOCAL_*`, `LOCAL_ALLOWED_STAGES`,
  `local_allowed`, `offered_tiers`, the `if LOCAL_BASE_URL:` tier registration,
  `valid_tier`, `PRICES_PER_MTOK` (`LOCAL_MODEL` → 0 price), `TIER_ORDER`,
  `next_tier_up`.
- `pipez/providers.py` lines 196–260: `OpenAICompatProvider.complete` — reads
  `tier.get("base_url")` but takes api key, temperature, timeout and
  `enable_thinking` from global `config.LOCAL_*`.
- `pipez/engine.py`: `__init__` lines 86–94 and `_probe_local` 96–110 (single
  `self._local_down` flag); `_llm_call` 111–143 and `_run_llm_call` 158–175 (the
  `name == "local" and self._local_down` fallback); line 267 (intake tier:
  `"local" if config.local_allowed("intake") else config.INTAKE_TIER`); line 450
  (brief tier); line 572 (`offered_tiers("plan")` in the plan prompt); lines
  843–846 (`_materialise_plan` coerces `tier == "local"` code tasks to `light`,
  logs `tier_coerced`); line 2111 (same rule in the code-task path).
- `pipez/llm.py` lines 81–88 `decision_schema` (tier enum from
  `offered_tiers("decide")`), line 290 provider registry.
- `scripts/bench_local.py` lines 126–140 (`parse_tier`, `tier_dict`) and 360–368
  (pre-flight env check that only knows `local`).
- Tests: `tests/test_providers.py`, `tests/test_escalation.py`,
  `tests/test_local_bench.py` (how `config` is patched per test; tests are
  env-independent — never assert literal `.env` values).
- `.env.example` lines 25–31; `README.md` lines 25–28 (env table) and the
  `## Local tier` section at ~150.

## Deliverables

1. **Config** (`pipez/config.py`):
   - New env vars `PIPEZ_LOCAL_REASON_BASE_URL`, `PIPEZ_LOCAL_REASON_MODEL`,
     `PIPEZ_LOCAL_REASON_API_KEY`, `PIPEZ_LOCAL_REASON_TIMEOUT`,
     `PIPEZ_LOCAL_REASON_TEMPERATURE`, `PIPEZ_LOCAL_REASON_THINKING`,
     `PIPEZ_LOCAL_REASON_REASONING_EFFORT` (one of low/medium/high or empty) and
     `PIPEZ_LOCAL_REASON_STAGES`. Also add `PIPEZ_LOCAL_REASONING_EFFORT` for the
     coder tier (default empty). Key/timeout/temperature for `local_reason` fall
     back to the `local` values when unset.
   - The `local_reason` tier exists only when `PIPEZ_LOCAL_REASON_BASE_URL` is set
     (mirror the `local` rule). Each local tier dict carries everything the provider
     needs: `provider`, `model`, `effort`, `base_url`, `api_key`, `timeout`,
     `temperature`, `thinking` (bool), `reasoning_effort` (str or None).
   - `LOCAL_TIERS = ("local", "local_reason")`; `is_local(name)`;
     `tier_allowed(tier, stage)` replacing `local_allowed` (keep `local_allowed` as a
     thin alias for `tier_allowed("local", stage)`); `offered_tiers(stage)` includes
     each local tier only when allowed; new `stage_tier(stage, default)` returns
     `local_reason` if allowed for that stage, else `local` if allowed, else
     `default`.
   - `TIER_ORDER = ["local", "local_reason", "light", "standard", "deep"]`.
   - Both local models get a 0 price in `PRICES_PER_MTOK`.
2. **Provider** (`pipez/providers.py`): `OpenAICompatProvider.complete` takes
   base_url, api_key, timeout, temperature, thinking and reasoning_effort from the
   tier dict, falling back to the `config.LOCAL_*` globals only when the key is
   absent. Send `chat_template_kwargs: {"enable_thinking": <bool>}` as today; send
   `reasoning_effort` only when the tier has one. Nothing else in the request body
   changes.
3. **Engine** (`pipez/engine.py`):
   - Probe every configured local tier at init (one probe per tier, same method);
     `self._local_down` becomes a `set` of tier names that failed the probe.
     `_llm_call` and `_run_llm_call` fall back with `next_tier_up` when
     `name in self._local_down` (log text unchanged apart from the tier name).
   - Lines 267 and 450: use `config.stage_tier("intake", config.INTAKE_TIER)` and
     `config.stage_tier("brief", "standard")`.
   - Lines 843–846 and 2111: coerce any `config.is_local(tier)` code task to `light`
     (same `tier_coerced` log, with the real `from` tier).
4. **Bench** (`scripts/bench_local.py`): the pre-flight check at ~363 requires the
   matching `*_BASE_URL`/`*_MODEL` for whichever local tiers are in `used`
   (message names the missing vars). `--tiers local_reason@high` must work
   (effort flows into the tier dict; the provider ignores `effort` and uses the
   tier's `reasoning_effort` — do not map one onto the other).
5. **Tests** (new `tests/test_local_tiers.py`, plus adjustments where existing tests
   assume a single local tier):
   - config: with both URLs set (monkeypatch env + `importlib.reload(config)`, as
     `test_local_bench.py` does), `MODEL_TIERS` has both tiers with the per-tier
     fields; `stage_tier("plan", "deep")` returns `local_reason` when
     `PIPEZ_LOCAL_REASON_STAGES=plan`, `local` when only `PIPEZ_LOCAL_STAGES=plan`,
     `deep` when neither; `offered_tiers("decide")` lists each local tier only when
     allowed; `next_tier_up("local") == "local_reason"` when configured and
     `"light"` when not.
   - provider: via `httpx.MockTransport`, a `local_reason` tier dict produces a
     request to its own base_url with its own bearer token, `reasoning_effort` set,
     and `enable_thinking` per its own flag; a `local` tier dict produces no
     `reasoning_effort` key.
   - engine: with stub providers, a `local_reason` task escalates
     `local_reason → light` on `LLMError`; a probe failure for `local_reason` alone
     leaves `local` usable and sends `local_reason` calls to `light`; a code task
     with tier `local_reason` is coerced to `light` and logged `tier_coerced`.
   - Existing suite stays green (`pytest -q`, ~6 min; the known timing-flaky
     `tests/test_resilience.py::test_stop_kills_remote_runner_group` may be rerun
     alone).
6. **Docs**: `.env.example` gains the `PIPEZ_LOCAL_REASON_*` block (commented out,
   like the `local` URL/model) and `PIPEZ_LOCAL_REASONING_EFFORT`; `README.md` env
   table and `## Local tier` section describe both tiers, `stage_tier` precedence
   (reasoner before coder), and that `reasoning_effort` is for gpt-oss-style models
   while `enable_thinking` is for Qwen-style ones. Replace the stale "about 9
   output tokens/s" sentence with "throughput depends on the serving host".

## Out of scope

- No prompt changes (`seed_prompts.py`), no new stages, no changes to the recursive
  decide/decompose logic or the plan path beyond the tier wiring above.
- No UI changes.
- Do not edit `.env` (gitignored, mode 600) — the user sets it. Do not deploy to the
  VM. Do not commit.
- Do not call the real inference host or Proxmox from tests; tests use
  `httpx.MockTransport` and stub providers only (conftest already forces the mock
  sandbox).
- Never `pkill`; never touch ports 8400/8401 or any VM.

## Verification criteria

- `python -c "import pipez.config"` works with no `PIPEZ_LOCAL*` vars set
  (`MODEL_TIERS` has exactly deep/standard/light) and with only
  `PIPEZ_LOCAL_REASON_BASE_URL`/`_MODEL` set (adds `local_reason` only).
- `grep -n '"local"' pipez/engine.py pipez/config.py scripts/bench_local.py` shows
  no remaining single-tier special case that should cover both tiers (string
  literals inside `LOCAL_TIERS`, aliases and log messages are fine).
- `pytest -q tests/test_local_tiers.py tests/test_providers.py tests/test_escalation.py tests/test_local_bench.py` passes.
- Full `pytest -q` passes (modulo the known flaky test, rerun alone).
- `python scripts/bench_local.py --help` still runs.

## Write summary

Last step, mandatory: write `.odin/sessions/task-29-local-tiers-summary.md` listing
every file changed, the new env vars, the test counts (before/after), and anything
left undone or uncertain.
