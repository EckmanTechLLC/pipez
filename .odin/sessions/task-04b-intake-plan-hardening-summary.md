# Task 04b summary: intake + plan hardening

## Files changed
- pipez/config.py: INTAKE_MAX_ROUNDS (2), PLAN_MAX_TASKS (12), PLAN_MAX_MODULES (8), PLAN_EFFORT (high).
- pipez/db.py: new `set_decisions(run_id, decisions)` (decisions only; answers/brief untouched).
- pipez/seed_prompts.py: `intake_key_vocabulary` prompt row (~21 keys); new paragraphs in intake_discovery, intake_brief, plan_architect.
- pipez/engine.py: `_known_keys_section` (resolve_all keys with value, source, n, skipping `open.*`, plus vocabulary) appended to the discovery prompt; locked exemption from `intake_invalid`; open questions -> `open.<slug>` free_text decisions (round-tracked, back to awaiting_human); `answer_intake` multi-round (prior answers kept and not re-answerable, free-text answers observed with scope `request`/source `intake`, previous decisions not re-observed); brief re-run with "Resolved open questions" section, appended to brief text; last round writes "## Assumptions" instead; planning appends HARD LIMITS to the system prompt, applies PLAN_EFFORT over the deep tier, logs `plan_oversize` and stores `plan_meta.warnings`.
- pipez/llm.py: MockLLM.brief returns one open question unless prompt contains "Resolved open questions".
- ui/index.html: free_text cards render a textarea; already-answered decisions disabled in later rounds; plan panel shows warning lines and `model · output tokens · seconds` line; textarea CSS.
- tests/test_intake.py (updated accept-all flow; new: open-question round trip, last-round assumptions, locked exemption, known-keys prompt), tests/test_plan.py (to_plan and API test handle second intake round; new plan_oversize test also checks HARD LIMITS text and effort).

## Decisions / interpretations
- Hard limits are appended in code to the plan_architect system prompt (so DB-edited prompts still get them); the prompt text itself refers to "HARD LIMITS".
- Round numbering: first brief = round 1 asks; with MAX_ROUNDS=2 the second brief converts leftover questions to assumptions.
- Open decisions carry `round` and a null prefill so accept_all skips them (required -> must be answered).
- Sources of earlier answers in the second-round decision table are reconstructed as request (locked) / intake; "override" provenance is not preserved there.
- Open-question observations use source `intake` (SOURCES has no other fit), scope `request`.
- seed_prompt updates unedited live prompts on restart, so the live DB gets new wording after the next restart (not touched now).

## Verification
1. pytest: 66 passed. Met.
2. Mock smoke on 8401: submit -> awaiting_human; accept_all -> awaiting_human with one `open.which_name_should_the_product_go` (free_text); answer -> awaiting_plan; brief contains "Resolved open questions". Met. 8401 stopped, temp DB removed, 8400 still returns 200 (untouched).
3. grep: intake_key_vocabulary in seed_prompts.py and engine.py; PLAN_MAX_TASKS in config.py and engine.py (not in seed_prompts.py, by design). Met.
4. node --check of inline script: OK. Met.
Not touched: pve.py, sandbox.py, git, live DB.

## New prompt paragraphs
intake_discovery (appended): "KNOWN KEYS: the input lists 'Known decision keys' - keys already in use, with their current resolved value where one exists. Whenever a decision is the same concept as a known key, reuse that key exactly (do not rephrase or respell it). Invent a new lowercase dotted key only for a genuinely new concept."

intake_brief (replaces the open_questions sentence): "Emit an open question ONLY when no option could reasonably be proposed for it; if you can propose a sensible default, write the brief on that default and say it is an assumption - the human reviews decisions, not guesses. If the input has a 'Resolved open questions' section, those answers are binding like decisions and must not be asked again. Do not reproduce the decision table; it is appended separately and exactly."

plan_architect (inserted before the REVISION NOTE sentence): "PARSIMONY: the limits stated under HARD LIMITS at the end of this prompt are binding. Merge small tasks: a task is a unit one engineer completes in one sitting, not a function. Do not add tooling, packaging, linters, type-checkers, adapters or integrations that the brief did not decide; if you believe one is needed, describe it under an OPTIONS heading inside `architecture` and keep it out of modules and tasks. When the brief names no framework, prefer the standard library."
Code-appended: "HARD LIMITS: at most 12 tasks and at most 8 modules. Merge small tasks; a task is a unit one engineer completes in one sitting, not a function."
