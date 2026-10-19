# Task 19 — Standing-preference suggestions from request text; normalised free-text answers

## Objective
Two memory gaps. (A) A request that states a durable preference ("I always want MIT",
"never use Docker in my projects") only locks that run's decision; it is never learned.
The intake call must now also return `standing_preferences` (key, value, verbatim
evidence) which are stored on the run and shown in the intake UI as suggestions with a
one-click **pin**. Nothing is pinned or observed automatically. (B) Free-text answers to
open questions (`free_text` decisions, key `open.*`) are stored request-scoped and never
learned. The brief call must now also return `normalised_answers` mapping such answers
onto known decision keys; those are recorded as ordinary global `intake` observations
(graduation rules apply) — never pinned.

## Reference (read first)
- `pipez/llm.py` — `INTAKE_SCHEMA` (~125), `BRIEF_SCHEMA` (~162), `LLM.intake` (~317,
  converts request_values list→dict), `LLM.brief`, `MockLLM.intake/brief` (~391/417).
- `pipez/seed_prompts.py` — `intake_discovery`, `intake_key_vocabulary`, `intake_brief`;
  `seed()` upserts only when the stored prompt equals the previous seeded text.
- `pipez/engine.py` — `_known_keys_section` (~225), `_intake` (~258; stores
  `meta` via `db.set_intake`), `answer_intake` (~300–370; `free_text` → observe scope
  `request`), `_brief_prompt` (~379), `_brief_and_start` (~397).
- `pipez/prefs.py` — `observe`, `pin`, `resolve_all`, `SCOPES`, `SOURCES`.
- `pipez/db.py` — `set_intake` (~342; `intake_meta = {"discovery": meta}`), `log`.
- `pipez/server.py` — `PUT /api/preferences/{key}` (pin), `GET /api/preferences`.
- `ui/index.html` — intake panel render (~795–880), preferences panel (~1164–1215,
  pin/retire handlers to reuse), `.dcard`, `.badge` styles.
- `tests/test_prefs.py`, `tests/test_intake*.py` (whatever exists) — fixture patterns.

## Deliverables
1. **Schema + prompts.** `INTAKE_SCHEMA.standing_preferences`: array (required, may be
   empty) of `{key, value, evidence}` with `additionalProperties: false`.
   `BRIEF_SCHEMA.normalised_answers`: array (required, may be empty) of
   `{key, value, from_key}`. `intake_discovery` gains a paragraph: standing preferences
   are only those the request states as durable (always / never / in all my projects /
   from now on / my standard); a one-off requirement for this build is NOT one;
   `evidence` is a verbatim quote from the request; reuse known keys. `intake_brief`
   gains a paragraph: when a resolved open question's answer clearly settles a known
   decision key, emit it in `normalised_answers` with `from_key` = the open.* key and
   `value` in the known key's vocabulary style; never invent keys; empty list otherwise.
   `MockLLM.intake` returns `[{"key": "legal.license", "value": "MIT", "evidence":
   "always MIT"}]` when the prompt contains "always MIT", else `[]`. `MockLLM.brief`
   returns `[{"key": "storage.database", "value": "sqlite", "from_key": <first open.*
   key present in the prompt>}]` when the prompt contains "use sqlite", else `[]`.
2. **Engine, intake.** In `_intake`, filter `standing_preferences`: drop entries without
   key/value, keys starting with `open.`, and keys already pinned to the same value
   (`prefs.resolve(key)` source `pinned`). Store the remainder in the intake meta as
   `standing_preferences` and `db.log(... "standing_prefs", {"keys": [...]})`. No
   observe, no pin.
3. **Engine, brief.** In `_brief_and_start`, after the brief call succeeds (and only on
   the path that proceeds past open questions, i.e. when the brief is final): for each
   `normalised_answers` item where `from_key` is a `free_text` decision of this run with
   an answer, `key` is not `open.*`, `key` is in the known keys (`_known_keys_section`
   source set: `prefs.resolve_all` keys ∪ `intake_key_vocabulary` lines), and `key` has
   no answer in this run → `prefs.observe(key, value, "global", "intake", project_id,
   run_id, reason="normalised from %s" % from_key)`. Log `normalised_answers`
   `{"accepted": [...], "rejected": [...]}`.
4. **UI.** In the intake panel, when `run.intake_meta.discovery.standing_preferences`
   is non-empty, render a block "Standing preferences stated in your request" above the
   decision cards: one row per item — key, value, quoted evidence, a **pin** button
   (`PUT /api/preferences/{key}` with `{value}`), and a **dismiss** button (hides the
   row for this run in `localStorage`, key `pipez.dismissed.<run_id>`, try/catch). After
   a successful pin, the row shows a `pinned` badge and the button disables. On render,
   fetch `/api/preferences` once and show items already pinned to the same value as
   `pinned` immediately.
5. **Tests** (`tests/test_standing_prefs.py`): (a) intake with "always MIT" in the
   request stores the suggestion in `intake_meta.discovery.standing_preferences` and
   creates no observation and no pinned pref; (b) an already-pinned same-value key is
   filtered out; (c) brief with a resolved open question and "use sqlite" records one
   global `intake` observation for `storage.database` with the reason, and none when
   `storage.database` already has an answer in the run or when `from_key` is not a
   free_text decision; (d) schemas: both new fields are in `required`. Full suite
   `python -m pytest -q` passes (~4–5 min; timeout ≥ 560 000 ms or nohup + poll; do
   not assume a hang before 8 minutes; rerun flaky failures).

## Out of scope
- Auto-pinning, changing graduation rules, DB schema changes (intake_meta is JSON).
- Any change to planning/running/review, the runner, scripts/bench_local.py, or
  `.odin/bench/`.
- Never run `pkill`/`killall`; never touch ports 8400/8401; never touch Proxmox.

## Verification criteria
- `grep -n standing_preferences pipez/llm.py pipez/engine.py pipez/seed_prompts.py
  ui/index.html` and `grep -n normalised_answers pipez/llm.py pipez/engine.py
  pipez/seed_prompts.py` all non-empty.
- `python -m pytest -q` green; new test file has ≥ 5 tests.
- A seeded fresh DB (`Database(tmp)` + `seed_prompts.seed`) has the new paragraphs in
  `intake_discovery` and `intake_brief`.

## Write summary
`.odin/sessions/task-19-standing-prefs-summary.md`: files changed, test count, the
exact prompt paragraphs added, anything deferred.
