# Task 04a (hotfix): strict-schema constraints rejected by the API

## Objective
The first live run failed at intake: `output_config.format.schema: For 'array'
type, 'minItems' values other than 0 or 1 are not supported (got: [2, 5])`.
Anthropic strict structured outputs reject numeric/length constraints. Remove
them from every schema in `pipez/llm.py` and carry the intent in `description`
text instead, then add a guard test so no schema ever contains them again.

## Reference
- `pipez/llm.py` line ~66 (`"minimum": 0` on `depends_on` integer items) and ~147 (`"minItems": 2, "maxItems": 6` on intake `options`); also scan `plan_schema()`, `BRIEF_SCHEMA`, `VERDICTS_SCHEMA`, `RESULT_SCHEMA` for any `minItems/maxItems/minimum/maximum/minLength/maxLength/pattern/format` keys.
- `pipez/engine.py` — where intake decisions are validated after the call (add a code-side check: a decision with fewer than 2 options is kept but logged `intake_invalid`; more than 6 are truncated).

## Deliverables
1. Remove every `minItems`, `maxItems`, `minimum`, `maximum`, `minLength`, `maxLength`, `pattern` from the schemas in `pipez/llm.py`; put the constraint into the property's `description` (e.g. "2–6 options", "non-negative indices of earlier subtasks").
2. Code-side enforcement in the engine for the two cases above (options count on intake decisions; negative/out-of-range `depends_on` is already dropped by 06a's validator — confirm, do not duplicate).
3. `tests/test_schemas.py`: walk `decision_schema()`, `INTAKE_SCHEMA`, `BRIEF_SCHEMA`, `plan_schema()`, `RESULT_SCHEMA`, `VERDICTS_SCHEMA` recursively and assert none of the forbidden keys appear; assert every object node has `additionalProperties: false`.

## Out of scope
Anything else. Do not touch the running server on port 8400; the controller restarts it.

## Verification criteria
1. `.venv/bin/python -m pytest -q tests/` passes (54 prior + new).
2. `grep -n "minItems\|maxItems\|\"minimum\"\|\"maximum\"" pipez/llm.py` → nothing.

## Write summary
`.odin/sessions/task-04a-schema-hotfix-summary.md` (short).
