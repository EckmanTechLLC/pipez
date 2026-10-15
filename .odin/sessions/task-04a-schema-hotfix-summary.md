# Task 04a summary
- pipez/llm.py: removed `minimum` (depends_on items) and `minItems/maxItems` (intake options); constraints moved to `description`. No other forbidden keys found in any schema.
- pipez/engine.py `_intake`: decisions with <2 options kept and logged `intake_invalid` (on `run:<id>`); >6 options truncated to 6.
- depends_on negative/out-of-range: confirmed handled by existing `_validate_deps` (not duplicated).
- tests/test_schemas.py (new): recursive walk of all six schemas; no forbidden keys, every object has additionalProperties false.

Verification: 1 met (55 passed = 54 + 1); 2 met (grep returns nothing).
Left alone: port 8400 server, infra/, everything else.
