# Task 34 summary — plan gate tuning

## Changed
- pipez/plangate.py: `module_kind(module)` (code|tests|docs|infra|unknown) plus helpers (`_file_kind`, `_owned_window`,
  `_files_of`); kinds applied in `check_plan`. `files? owned` window now also matches "Owned files".
  - contract_interface skipped (no warning) for tests/docs/infra.
  - dep_coverage: deps into docs/infra/tests ignored; tasks on docs/infra exempt.
  - consumer_names_interface: docs/infra exempt; tests module satisfied by naming any test file path.
  - task_prompt_thin: error only if short AND names nothing from own contract; warn if exactly one; message lists the
    failed condition(s). docs/infra tasks exempt; tests tasks skip the length rule and are checked against own +
    dependency symbols.
- pipez/config.py: PIPEZ_PLAN_GATE_ROUNDS default 2 -> 3. README.md (rounds, module kinds, thinness rule), .env.example.
- tests/test_plan_gate.py: +16 tests (module_kind parametrised x10, noncode skips, dep exemptions, tests-module rule,
  thin severity 300/named, 300/nameless, 600/nameless, rounds default, gptoss replay).
- tests/fixtures/plans/{c7213225e46a,60cd5166f16a,c6973d74ac0e}-gptoss-r2.json (new).

## Fixture errors (after tuning)
- 58b-opus: [dep_coverage:T7] (unchanged). c7-opus: [] (unchanged).
- 58b-qwen: 8 errors (dep_coverage T1 x4, contract_interface server+client, thin T1, T8). Before: 14 (thin T1-T8 as errors).
- c7-gptoss: task_module_unknown:T7, consumer_names_interface:app, thin:T6. c7-qwen: 9 errors (was 14ish).

## Decisions / deviations (please read)
- The saved `output` in bench-20261106-180234.json is the FIRST-round plan, not the post-revision plan (the bench
  stores `output` before `_gate_loop`; only `gate_violations` are final-round). So the "-r2" fixtures are first-round
  plans and the task's replay targets cannot hold: c7213225e46a still has 3 errors (contract_interface:app because
  its prompts/contract mention routes without shapes; thin T10, T11 short and nameless), 60cd5166f16a has 6
  task_module_unknown errors, c6973d74ac0e has 3 task_module_unknown + 3 dep_coverage. None are tests/infra
  contract_interface. I did not loosen anything to force them; the replay test asserts only that no
  contract_interface error lands on a tests/infra/docs module and that c6973's tests->money dep_coverage stays checked.
  Fixture names kept as specified despite the misnomer. Truly replaying the final-round plans needs the bench to save
  the final plan (out of scope).
- Tests tasks that name nothing from own/dependency contract are an error (literal "must still name"), even when long.
- Symbol length filter (>=4) means 3-letter names like `add` do not count as naming a symbol; tests use file paths.
- A module path that is itself tests/docs/infra is decisive (avoids a Dockerfile CMD mentioning src/server.js making
  infra look like code). Opus 58b `docs` module classifies as infra (Dockerfile+README mix); outcomes unchanged.

## Verification
- Opus one-liners print ['dep_coverage:T7'] and []: met.
- test_plan_gate + test_plan_rules + test_local_bench: 53 passed (was 37 before, +16): met.
- Full suite: 257 passed (was 241 incl. flaky-free run; +16): met.
- Replay criteria (c7213225e46a 0 errors, others <=1): NOT met, see above (input plans are first-round).

## Not done
- No commit/deploy/.env edit. Engine, bench, prompts untouched.
