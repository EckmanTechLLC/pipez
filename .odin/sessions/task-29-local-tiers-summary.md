# Task 29 summary: two local tiers

## Files changed
- pipez/config.py: LOCAL_TIERS, is_local, tier_allowed, local_allowed (alias), stage_tier, offered_tiers; per-tier
  dicts for `local` and `local_reason`; LOCAL_REASON_* globals; both models priced 0; TIER_ORDER includes local_reason.
- pipez/providers.py: OpenAICompatProvider reads base_url/api_key/timeout/temperature/thinking/reasoning_effort from the
  tier dict (globals only when key absent); `reasoning_effort` sent only when set; `healthy(tier=None)` probes the tier's URL.
- pipez/engine.py: `_local_down` is a set; one probe task per configured local tier (`_probe_local(name="local")`);
  fallback in `_llm_call`/`_run_llm_call` by `name in _local_down`; intake/brief use `config.stage_tier`;
  `_materialise_plan` and `_leaf_model` coerce any local tier to light (tier_coerced logs real `from`).
- scripts/bench_local.py: pre-flight names missing `PIPEZ_LOCAL[_REASON]_BASE_URL/_MODEL` per used tier; unknown-tier message updated.
- tests/test_local_tiers.py (new, 11 tests); tests/test_escalation.py (stub `healthy(tier=None)`, `_local_down == set()`).
- .env.example, README.md (env table, "Local tiers" section, stale 9 tok/s sentence replaced).

## New env vars
PIPEZ_LOCAL_REASONING_EFFORT, PIPEZ_LOCAL_REASON_BASE_URL/_MODEL/_API_KEY/_TIMEOUT/_TEMPERATURE/_THINKING/_REASONING_EFFORT/_STAGES.

## Decisions
- Invalid reasoning-effort values are treated as unset. `_probe_local` takes an optional tier name (default "local").
- Empty-string REASON key/timeout/temperature fall back to the local values.
- Test config fixture blanks vars (not deletes) so config's `.env` loader cannot refill them; reloads config at teardown.

## Verification
- Config import with no PIPEZ_LOCAL vars: tiers = deep/standard/light. Met (checked with blank vars).
- Only REASON URL/MODEL: adds local_reason only. Met.
- grep '"local"' in engine/config/bench: only LOCAL_TIERS, alias, probe default arg, bench tier table. Met.
- Four targeted test files: 34 passed. Met.
- Full pytest -q: 203 passed (331s), flaky test did not fail. Met. Before: not separately counted (collect-only on stash was attempted; see below).
- `bench_local.py --help` runs. Met.

## Uncertain / left alone
- Escalation `local -> local_reason` still goes to a tier known to be down (only the entry fallback checks `_local_down`, as specified).
- `.env` untouched, nothing committed or deployed. Baseline test count: 203 total now includes 11 new tests (192 before).
