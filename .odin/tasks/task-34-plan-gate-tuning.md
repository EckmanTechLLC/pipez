# Task 34 — Plan gate tuning: non-code modules, prompt-thinness severity, round budget

## Objective

The convergence bench (`.odin/sessions/bench-local-plan-2026-11-06/NOTES.md`, last section)
shows the gate's residue on gpt-oss plans after two revision rounds is mostly
`contract_interface` on modules that have no code interface by nature (`tests`, `infra`,
`docs`, `README`-type modules) and `dep_coverage` edges into such modules, while qwen stalls
on `task_prompt_thin` by character count alone. Tune the gate so that (a) a module whose
owned files are not code is held to a different standard, (b) prompt thinness is an error only
when the prompt is both short and names nothing from its module's contract, and (c) the
engine's default round budget is 3. Keep the five fixtures' key outcomes: Opus tic-tac-toe
exactly `dep_coverage:T7`, Opus shortener zero errors, local plans still well above zero.

## Reference (read first)

- `pipez/plangate.py` — whole file (`check_plan`, the regex constants, `_symbols`,
  `_has_interface`, `_has_wire_interface`, `_CODE_FILE_RE`, the "files owned" window).
- `pipez/config.py` plan-gate block (~241–245).
- `tests/test_plan_gate.py` and `tests/fixtures/plans/*.json`.
- `.odin/sessions/task-32-plan-gate-summary.md` (per-fixture outcomes and the narrowing
  decisions already made) and the final-round violation lists in
  `.odin/sessions/bench-local-plan-2026-11-06/bench-20261106-180234.json`
  (`results[*].gate_violations`), which are the false positives to remove.
- `README.md` plan-gate paragraph; `.env.example`.

## Deliverables

1. **Module kinds**: `module_kind(module) -> "code" | "tests" | "docs" | "infra" | "unknown"`
   derived from the owned-files window of the contract plus the module `path`/`name`:
   `code` when any owned file matches `_CODE_FILE_RE` and is not under a test directory;
   `tests` when the owned files are test files/dirs (`test/`, `tests/`, `__tests__/`,
   `*.test.*`, `*_test.*`, `spec/`) or the name is `tests`/`test`; `docs` for
   `README*`, `LICENSE*`, `docs/`, `*.md` only; `infra` for `Dockerfile`, `docker-compose*`,
   `deploy/`, `*.service`, `Makefile`, CI files, `.env*`; `unknown` otherwise (treated as
   `code`).
   - `contract_interface` is checked only for `code`/`unknown` modules. For `tests`, `docs`,
     `infra` it is skipped entirely (no warning).
   - `dep_coverage`: a dependency *into* a `docs` or `infra` module is ignored; into a
     `tests` module too (tests depend on code, not the reverse). A task *on* a `docs`/`infra`
     module is exempt from `dep_coverage` and `task_prompt_thin`; a task on a `tests` module
     is exempt from `task_prompt_thin`'s length rule but must still name a symbol or file
     from the module it tests (its own module's contract or any dependency's).
   - `consumer_names_interface`: a `tests` module's dependency edges are satisfied if its
     contract or task prompts name the dependency module *or* any test file path; `docs`
     and `infra` modules are exempt.
2. **`task_prompt_thin` severity**: `error` only when the prompt is shorter than
   `PLAN_GATE_MIN_PROMPT_CHARS` **and** names no symbol/file from its module's contract;
   `warn` when exactly one of the two holds. Message states which condition(s) failed.
3. **Config**: `PLAN_GATE_MAX_ROUNDS` default 3 (env unchanged). Document.
4. **Tests**: extend `tests/test_plan_gate.py`:
   - `module_kind` unit cases for each kind, including a `tests` module whose name is
     `apiTests` with owned file `test/api.test.js`, and a `docs` module named `docs` with
     `README.md`/`LICENSE`.
   - Fixture outcomes after tuning: Opus tic-tac-toe errors == `['dep_coverage:T7']`; Opus
     shortener errors == []; qwen tic-tac-toe ≥ 6 errors still including `contract_interface`
     for `server` and `client`; gpt-oss shortener still has `task_module_unknown:T7`.
   - Replay the saved final-round gpt-oss plans from
     `.odin/sessions/bench-local-plan-2026-11-06/bench-20261106-180234.json` (copy the
     `output` of the `local_reason` results for `c7213225e46a`, `60cd5166f16a`,
     `c6973d74ac0e` into `tests/fixtures/plans/*-gptoss-r2.json`): after tuning,
     `c7213225e46a` has 0 errors; the other two have at most 1 error each, and none of the
     remaining errors is `contract_interface` on a `tests`/`infra` module.
   - Severity test: a 300-char prompt that names a contract symbol → `warn`; a 300-char
     prompt naming nothing → `error`; a 600-char prompt naming nothing → `warn`.
5. **Docs**: README plan-gate paragraph: module kinds and what each is exempt from; the new
   thinness rule; default rounds 3.

## Out of scope

- No engine, bench, prompt or schema changes. No `.env` edits, no commit, no deploy. Never
  `pkill`; never touch ports 8400/8401, any VM or the Proxmox host; tests must not reach
  the network.

## Verification criteria

- `.venv/bin/python -c "import json,pipez.plangate as g; p=json.load(open('.odin/bench/plans/58b9853f2c47-opus.json')); print([x['code']+':'+x['subject'] for x in g.check_plan(p) if x['severity']=='error'])"`
  prints `['dep_coverage:T7']`; the same for `c7213225e46a-opus.json` prints `[]`.
- `.venv/bin/pytest -q tests/test_plan_gate.py tests/test_plan_rules.py tests/test_local_bench.py` passes.
- Full `.venv/bin/pytest -q` passes (known flaky `test_stop_kills_remote_runner_group` may
  be rerun alone).

## Write summary

Last step, mandatory: `.odin/sessions/task-34-plan-gate-tuning-summary.md` — files changed,
per-fixture error lists before/after, test counts before/after, anything undone or uncertain.
