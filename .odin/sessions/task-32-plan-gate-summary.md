# Task 32 summary — mechanical plan gate

## Changed
- pipez/plangate.py (new): `check_plan`, `format_note`, plus `heuristic_violations`/`heuristic_warnings`
  (the moved task_touches_deployment / process_acceptance_criteria regexes and logic).
- pipez/config.py: PLAN_GATE_MODE (off|warn|revise, invalid -> warn), PLAN_GATE_MAX_ROUNDS (2),
  PLAN_GATE_MIN_PROMPT_CHARS (400, env PIPEZ_PLAN_GATE_MIN_PROMPT_CHARS).
- pipez/engine.py: `_plan` runs the gate (warn / revise loop, `plan_meta.gate`, `plan_gate_revise` and
  `plan_call` with `gate_round` logs, spend per call); `_revision_sections` helper shared by human and gate notes,
  `_plan_prompt(run, gate_plan, gate_note)`; `_plan_rule_warnings` is now a thin wrapper over plangate;
  `_gate_round` helper. Regex class attributes removed from Engine. `off` mode keeps the old warnings path.
- ui/index.html `planMetaLines`: gate line + violations (`.err` for errors, existing `.summary` muted style for
  warnings); warnings already shown as gate violations are not repeated.
- tests/test_plan_gate.py (new, 14 tests), tests/fixtures/plans/ (5 plan copies), .env.example, README.md.

## Decisions (fixtures forced three narrowing/deviations from the task text)
- dep_coverage: "each dependency module" fails the shipped Opus plans (58b T5/T6, c7 T4/T5 have no edge to every
  dependency module). Narrowed: per dependency module D, a task is covered if it reaches a task of D through
  depends_on OR its prompt names an identifier/route of D's contract (files excluded). Result: only 58b T7 fails
  (its prompt restates no server symbol); c7 Opus is clean (its T4/T5 prompts restate openDb/createApp etc.).
- The qwen 58b fixture has module `client` with depends_on [] so a dep_coverage error "for the client task" is
  impossible; the test asserts dep_coverage on T1 (server, no task deps at all) instead.
- contract_interface: backticks are optional (the 58b Opus contracts contain none); signatures need `name(` with no
  space and exclude generic names. Modules that are HTTP/WebSocket-facing (code files owned + websocket/route/
  endpoint/http server wording in contract or tasks) need a route, message shape or messages/emits list, and are
  an error even with no dependents. This is what makes qwen `server` and `client` errors; docs-only modules
  are exempt (only warn).
- manual_testing_only also warns on the Opus server/docker modules (warnings are allowed).

## Per-fixture violations (errors; warnings omitted)
- 58b-opus: dep_coverage T7. (warn: contract_interface docs, manual_testing_only server/client)
- 58b-qwen: dep_coverage T1 x4 (one per dependency module), contract_interface server/client,
  task_prompt_thin T1-T8.
- c7-gptoss: task_module_unknown T7, consumer_names_interface app, task_prompt_thin T1-T7.
- c7-opus: none (warn: contract_interface docker, manual_testing_only server/docker).
- c7-qwen: dep_coverage T1 x2, contract_interface routes/app/server, consumer_names_interface routes,
  task_prompt_thin T1-T11.

## Verification
- Opus 58b one-liner prints ['dep_coverage:T7']: met. Qwen 58b: 14 errors incl. dep_coverage, contract_interface: met.
- test_plan_gate + test_plan_rules + test_local_tiers: 29 passed: met.
- grep PLAN_GATE: mode read only in `_plan`: met (MAX_ROUNDS also read there; MIN_PROMPT_CHARS in plangate).
- Full suite: 236 passed (was 222 before task 32, +14 new gate tests): met.

## Not done / uncertain
- UI change not exercised in a browser. Gate heuristics are tuned on five plans only. Nothing committed/deployed;
  scripts/bench_local.py and .env untouched. Test counts: targeted three files 15 -> 29
  (test_plan_gate.py 14 new); full suite 222 -> 236.
