# Task 31 summary — per-tier structured mode + client-side schema validation

## Changed
- requirements.txt: added `jsonschema>=4.23`; installed into .venv (4.26.0).
- pipez/config.py: `STRUCTURED_MODES`, `_structured()` (invalid -> default + warning), `LOCAL_STRUCTURED`
  (default json_schema), `LOCAL_REASON_STRUCTURED` (default json_object); both local tier dicts carry `structured`.
- pipez/providers.py: new `ProviderSchemaError(LLMError)` (retryable=False). `OpenAICompatProvider.complete`:
  json_schema body unchanged; json_object sets `{"type":"json_object"}` + appends "## Output format" section with
  the schema to the user message; prompt sets no response_format + same section. In non-grammar modes, content is
  cut from first `{` to last `}` before json.loads. All modes validate against the schema (Draft7 via
  validator_for default), raising ProviderSchemaError (json_path, message, mode, model), first 500 chars logged at
  debug. `usage["structured"]` set.
- scripts/bench_local.py: result records include `structured` when usage has it.
- tests/test_local_structured.py (new): config defaults/override/invalid, body per mode, fence/prose parsing,
  invalid-JSON in json_schema mode, schema violations (missing key, bad nested enum) x 3 modes with one facade attempt.
- tests/test_providers.py: `test_think_stripped_and_usage` exact-equality assertion on usage updated to include the
  new `structured` key (unavoidable consequence of the spec).
- .env.example, README.md "Local tiers": new vars, modes, defaults rationale, client-side validation.

## Decisions
- Engine untouched: `_add_spend` and log rows read named usage keys only, so the extra key is ignored.
- Unknown/absent `structured` in a tier dict falls back to json_schema in the provider.
- A schema that is itself invalid is logged as a warning and validation is skipped (not a failure).

## Verification
- import jsonschema works; requirements lists it: met.
- Targeted five test files: met (39 passed).
- Config print: met ({'deep': None, 'standard': None, 'light': None, 'local': 'json_schema', 'local_reason': 'json_object'}).
- Full suite: met (222 passed in 357s).
- Test counts: targeted files 29 before -> 39 after (incl. 10 new in test_local_structured.py).

## Left alone
- .env, Anthropic provider, seed_prompts, engine, schemas; nothing committed or deployed.
