# Task 31 — Per-tier structured-output mode for local tiers + client-side schema validation

## Objective

`OpenAICompatProvider` always requests `response_format: {type: "json_schema", strict: true}`.
On llama.cpp that compiles the schema to a grammar; gpt-oss-120b degenerates under it (opens
the first long string field and never closes it, burning the whole `max_tokens`), while the
same prompt with `response_format: {type: "json_object"}` or with no `response_format` and the
schema pasted into the prompt returns a valid plan in ~100 s / ~2.7k tokens (evidence:
`.odin/sessions/bench-local-plan-2026-11-06/NOTES.md`, probe2.log). Add a per-tier
`structured` mode — `json_schema` (today's behaviour, default for `local`), `json_object`
(default for `local_reason`) or `prompt` (no response_format) — and, because the server no
longer guarantees shape in the two non-grammar modes, validate every local response against
the call's schema client-side with the `jsonschema` library. Invalid output is a
non-retryable `LLMError` so the engine escalates, exactly like a timeout.

## Reference (read first)

- `pipez/providers.py`: `OpenAICompatProvider.complete` (~228–290): `g(tier, key, default)`,
  body construction (`response_format`, `chat_template_kwargs`, `reasoning_effort`,
  `max_tokens`), `_THINK_RE` strip, `json.loads(content)` → `LLMError("local provider
  returned invalid JSON")`; `ProviderTimeoutError` / `ProviderTruncatedError` with
  `retryable = False` (task 30).
- `pipez/config.py`: local-tier block (`LOCAL_*`, `LOCAL_REASON_*`, both tier dicts,
  `LOCAL_TIERS`), the `_effort()` / `_stages()` helpers used for env parsing.
- `pipez/llm.py`: `LLM._call` (~298–325, the `retryable is False` early re-raise), schema
  builders `decision_schema()`, `plan_schema()`, `INTAKE_SCHEMA`, `BRIEF_SCHEMA`,
  `VERDICTS_SCHEMA` — all are plain JSON-Schema dicts with `additionalProperties: False`.
- `requirements.txt` (anthropic, fastapi, uvicorn — pinned style), `requirements-dev.txt`;
  `infra/deploy-pipez.sh` installs `requirements.txt` into the VM venv (no change needed).
- Tests: `tests/test_local_bounds.py`, `tests/test_local_tiers.py` (config reload pattern),
  `tests/test_providers.py` (MockTransport; project imports `httpx2 as httpx`).
- `.env.example`, `README.md` "Local tiers" section.
- `.odin/sessions/task-30-local-call-bounds-summary.md`.

## Deliverables

1. **Dependency**: add `jsonschema>=4.23` to `requirements.txt` and install it into the
   project venv (`.venv/bin/pip install -r requirements.txt`). Use `jsonschema.Draft7Validator`
   (or `validator_for(schema)`); do not pull in extra format checkers.
2. **Config**: `PIPEZ_LOCAL_STRUCTURED` (default `json_schema`) and
   `PIPEZ_LOCAL_REASON_STRUCTURED` (default `json_object`), validated against
   `{"json_schema", "json_object", "prompt"}` (invalid → default, warn once like
   `PIPEZ_PRICES_JSON`). Each local tier dict carries `structured`.
3. **Provider** (`OpenAICompatProvider.complete`):
   - `json_schema`: body unchanged from today.
   - `json_object`: `response_format: {"type": "json_object"}` and the schema appended to the
     user message as a trailing section:
     `\n\n## Output format\nRespond with ONLY one JSON object, no prose and no code fence, that
     validates against this JSON Schema:\n<json.dumps(schema)>`.
   - `prompt`: no `response_format`; same appended section.
   - After the existing think-strip, in the two non-grammar modes also tolerate a leading
     ```` ```json ```` / trailing ```` ``` ```` fence and leading/trailing prose by taking the
     substring from the first `{` to the last `}` before `json.loads`.
   - **Validate the parsed object against `schema` in all three modes** (cheap, and it also
     catches grammar-mode servers that cut corners). On failure raise
     `ProviderSchemaError(LLMError)` with `retryable = False`, message = the first validation
     error's `json_path`/`message` plus the mode and model; log the offending content's first
     500 chars at `debug`.
   - Record the mode used in the returned `usage` dict as `usage["structured"]` so the engine's
     spend/log rows can carry it (no engine changes required for this; the key is simply
     passed through where `usage` is stored — check `_add_spend`/`add_tokens` tolerate an
     extra key and ignore it, otherwise drop it before storing).
4. **Bench**: `scripts/bench_local.py` results records include `structured` (from usage)
   when present. No new flags.
5. **Tests** (`tests/test_local_structured.py`, new, MockTransport only):
   - config: defaults (`local` → `json_schema`, `local_reason` → `json_object`), env override
     to `prompt`, invalid value falls back to the default.
   - provider body per mode: `json_schema` body identical to before (has `json_schema` key, no
     appended section); `json_object` body has `{"type":"json_object"}` and the user message
     ends with the schema; `prompt` body has no `response_format` and the appended schema.
   - parsing: fenced ```` ```json {...} ``` ```` content and `Here is the plan: {...}` content
     both parse in `prompt` mode; in `json_schema` mode a non-JSON body still raises the
     existing invalid-JSON `LLMError`.
   - validation: content that is valid JSON but violates the schema (missing required key;
     wrong enum value in a nested item) raises `ProviderSchemaError`, `retryable is False`,
     and `LLM._call` makes exactly one attempt (reuse the stub pattern from
     `tests/test_local_bounds.py`); content that validates returns normally with
     `usage["structured"]` set.
   - every existing test stays green.
6. **Docs**: `.env.example` (two vars with the allowed values), `README.md` "Local tiers":
   what each mode sends, the defaults and why (llama.cpp grammar vs gpt-oss), that all local
   output is schema-validated client-side and invalid output escalates.

## Out of scope

- No change to the Anthropic provider, prompts in `seed_prompts.py`, engine routing, or the
  schemas themselves.
- Do not edit `.env`; do not commit; do not deploy; never `pkill`; never touch ports
  8400/8401, any VM or the Proxmox host; tests must not reach the network.

## Verification criteria

- `.venv/bin/python -c "import jsonschema"` works; `requirements.txt` lists it.
- `.venv/bin/pytest -q tests/test_local_structured.py tests/test_local_bounds.py tests/test_local_tiers.py tests/test_providers.py tests/test_escalation.py` passes.
- Full `.venv/bin/pytest -q` passes (known flaky `test_stop_kills_remote_runner_group` may be
  rerun alone).
- `.venv/bin/python -c "from pipez import config; print({k:v.get('structured') for k,v in config.MODEL_TIERS.items()})"`
  prints `json_schema` for `local`, `json_object` for `local_reason`, `None` for Anthropic tiers.

## Write summary

Last step, mandatory: `.odin/sessions/task-31-local-structured-mode-summary.md` — files
changed, new env vars and dependency, test counts before/after, anything undone or uncertain.
