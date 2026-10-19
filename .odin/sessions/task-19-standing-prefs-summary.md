# Task 19 summary

## Changed
- pipez/llm.py: INTAKE_SCHEMA.standing_preferences, BRIEF_SCHEMA.normalised_answers (both required, additionalProperties false); MockLLM.intake/brief per spec (added `import re`).
- pipez/seed_prompts.py: paragraphs appended to intake_discovery and intake_brief.
- pipez/engine.py: `_intake` filters and stores standing_preferences in intake meta + logs `standing_prefs`; new `_record_normalised` called from `_brief_and_start` on the final-brief path (logs `normalised_answers` accepted/rejected); `_brief_prompt` resolved-open-question lines now include `(key: open.x)`.
- ui/index.html: "Standing preferences stated in your request" block with pin/dismiss, pinned badge, one-time /api/preferences fetch, localStorage `pipez.dismissed.<run_id>` (try/catch).
- tests/test_standing_prefs.py: 9 tests.

## Decisions
- The brief prompt previously did not expose open.* keys, so neither the real LLM nor the mock could produce `from_key`; I added the key to each resolved-question line (needed for the mock rule "first open.* key in the prompt").
- Pinned filter: dropped only when source is pinned and value equals.
- Dismiss hides the row immediately (DOM removal) and persists the key list per run.

## Prompt paragraphs added
- intake_discovery: "STANDING PREFERENCES: separately, list in standing_preferences only those preferences the request states as durable - worded as always / never / in all my projects / from now on / my standard. A requirement for this one build is NOT a standing preference. Each has a key (reuse a known key), a value, and evidence, a verbatim quote from the request. Return an empty list when there are none."
- intake_brief: "NORMALISED ANSWERS: when a resolved open question's answer clearly settles a known decision key, emit it in normalised_answers with from_key set to the open.* key of that question and value in the known key's vocabulary style. Never invent keys. Return an empty list otherwise."

## Verification
- greps for standing_preferences (llm, engine, seed_prompts, ui) and normalised_answers (llm, engine, seed_prompts): met, all non-empty.
- Full suite: 157 passed (286s). New file has 9 tests: met.
- Seeded fresh DB contains both paragraphs: met (test_seeded_prompts_have_paragraphs).
- UI not exercised in a browser; JS written but only reviewed by reading.

## Left alone
- scripts/bench_local.py, .odin/bench/, prior uncommitted changes; nothing committed.
