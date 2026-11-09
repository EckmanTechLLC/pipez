# Task 32 — Mechanical plan gate: validators + gate-driven revision rounds

## Objective

The planner's output is checked today only for shape (`_validate_plan`) and two heuristic
warnings (`_plan_rule_warnings`). Plans from local models (and one Opus plan that shipped a
broken UI) pass those checks while lacking the specificity that makes parallel tasks
integrate: a task on module `client` with `depends_on: []` although `client` depends on
`server`; a contract that is a file list with no interface; a 237-character task prompt that
says "handle WebSocket communication". Add a deterministic gate — pure functions over the
plan dict, no LLM — that turns those gaps into structured violations, and let the engine feed
the violations back to the planner as a machine-written revision note for a bounded number
of rounds before the plan reaches the human gate. Everything is recorded in `plan_meta` so a
benchmark (next task) can measure whether a given planner tier converges.

## Reference (read first)

- `pipez/engine.py`: `_plan` (~582–628: the plan call, `_validate_plan`, warnings,
  `set_plan`, `awaiting_plan`); `_plan_rule_warnings` (~702–721) and the `_DEPLOY_RE` /
  `_AUTHOR_RE` / `_PROCESS_RE` class attributes it uses; `_validate_plan` (~723–790);
  `_is_revision` / `_plan_prompt` (~551–580: how a REVISION NOTE and the previous plan are
  appended); `answer_plan` (~790–822: the human reject path appends to
  `plan_meta.revisions` and re-spawns `_plan`); `_materialise_plan` (~823+: how task
  `depends_on` becomes the DAG).
- `pipez/llm.py` `_PLAN_SCHEMA` (~201–270): module fields `name, path, purpose, depends_on,
  contract`; task fields `id, title, module, prompt, tier, effort, depends_on, acceptance`.
- `pipez/config.py` plan block (~232–237): `PLAN_MAX_TASKS`, `PLAN_MAX_MODULES`,
  `PLAN_EFFORT`, `PLAN_REVISION_TIER`, `PLAN_REVISION_EFFORT`.
- `pipez/db.py` `set_plan(run_id, plan, meta, revisions=None)` (~358).
- `ui/index.html` ~1150–1180: `planMetaLines` renders `plan_meta.warnings` as `.err` divs.
- Reference plans (read all five; they are the fixtures): `.odin/bench/plans/`
  `58b9853f2c47-opus.json` (shipped; its T7 client task has `depends_on: []` while module
  `client` depends on `server` — the gate must flag exactly that and little else),
  `58b9853f2c47-qwen.json` and `c7213225e46a-qwen.json` (thin contracts, short prompts),
  `c7213225e46a-gptoss.json` (tasks with `module: "none"`), `c7213225e46a-opus.json`
  (should pass or nearly pass).
- Tests: `tests/test_plan_rules.py` (how plan-level checks are tested today),
  `tests/test_local_tiers.py` (config reload pattern), `tests/test_escalation.py` (stub LLM
  pattern for engine tests).
- `.odin/sessions/bench-local-plan-2026-11-06/NOTES.md` for the motivating evidence.

## Deliverables

1. **`pipez/plangate.py`** (new, no engine imports, no I/O): `check_plan(plan: dict) ->
   list[dict]`; each violation is `{"code", "severity" ("error"|"warn"), "subject"
   (task id or module name), "message", "hint"}` where `hint` is one sentence telling the
   planner what to change. Checks, in this order:
   - `module_dep_unknown` (error): a module `depends_on` a name that is not a module.
   - `task_module_unknown` (error): a task's `module` is not a module name (`"none"`,
     `"*"`, empty included) — every task must own one module.
   - `dep_coverage` (error): for task T on module M and each module D in M.depends_on that
     has at least one task, T must depend — directly or transitively through task
     `depends_on` — on at least one task of D. Message names T, M, D and the candidate task
     ids of D.
   - `contract_interface` (error when some other module depends on this one, else warn): the
     module contract must enumerate a public interface: at least one backticked identifier
     followed by `(`, OR a backticked object/message shape (`{` … `}`), OR an HTTP route
     (`GET|POST|PUT|PATCH|DELETE` + a path), OR an explicit `exports`/`emits`/`messages`
     list. A contract that only lists files or says "manual testing" does not qualify.
   - `contract_files` (warn): the contract names no owned file (nothing matching a path
     with `/` or a filename with an extension).
   - `consumer_names_interface` (error): for module M depending on D, the text of M's
     contract plus the prompts of M's tasks must mention D's name or at least one
     backticked symbol taken from D's contract.
   - `task_prompt_thin` (error): task prompt shorter than `PLAN_GATE_MIN_PROMPT_CHARS`
     (config, default 400) OR mentioning no backticked symbol/file that appears in its
     module's contract.
   - `acceptance_not_checkable` (warn): a task whose acceptance list has no item containing
     a backticked command, an HTTP status code/path, or a file path.
   - `manual_testing_only` (warn): a module contract whose only testing statement is
     manual/README-checklist testing.
   Fold the two existing heuristics (`task_touches_deployment`, `process_acceptance_criteria`)
   in as warnings by moving their regexes and logic from `engine._plan_rule_warnings` into
   `plangate` (keep `_plan_rule_warnings` as a thin wrapper returning the warning
   messages so nothing else changes). Also `format_note(violations) -> str`: a REVISION
   NOTE body — header "AUTOMATED PLAN GATE — the previous plan failed these checks; fix every
   ERROR, keep everything else unchanged", then one line per violation
   `- [ERROR|WARN] code (subject): message — hint`.
2. **Config**: `PLAN_GATE_MODE` (`PIPEZ_PLAN_GATE`: `off` | `warn` | `revise`, default
   `warn`), `PLAN_GATE_MAX_ROUNDS` (`PIPEZ_PLAN_GATE_ROUNDS`, default 2),
   `PLAN_GATE_MIN_PROMPT_CHARS` (default 400).
3. **Engine** (`_plan`): after `_validate_plan`, run `plangate.check_plan`. In `warn` mode
   append every violation's `"[code] subject: message"` to `warnings` (errors first). In
   `revise` mode, while there are errors and fewer than `PLAN_GATE_MAX_ROUNDS` gate rounds
   have run for this plan: log `plan_gate_revise` (`{"round", "errors", "warnings"}`), call
   the planner again with the same system prompt and the current prompt plus
   `# Previous plan` (JSON) and `REVISION NOTE (automated plan gate):` +
   `format_note(violations)` — reuse `_plan_prompt`'s assembly by factoring the
   previous-plan/note section into a helper that takes the note text, so human notes and
   gate notes render the same way — on `PLAN_REVISION_TIER`/`PLAN_REVISION_EFFORT`, then
   re-validate and re-check. Record in `plan_meta["gate"] = {"mode", "rounds": [{"errors":
   n, "warnings": n, "violations": [...]}, ...], "passed": bool}` (round 0 is the first
   plan). Never fail the run on gate errors: after the last round the plan goes to
   `awaiting_plan` with its remaining violations in `warnings`. Spend for gate rounds is
   recorded like any plan call (`_add_spend(..., "plan", usage)`); log `plan_call` with
   `"gate_round": n`.
4. **UI**: `planMetaLines` shows gate state when `plan_meta.gate` exists: one line
   `Plan gate: passed after N rounds` or `Plan gate: N errors, M warnings remain`, then the
   violations with `.err` for errors and a muted style for warnings (reuse existing classes;
   no new CSS beyond one muted class if none fits).
5. **Tests** (`tests/test_plan_gate.py`, new; copy the five reference plans into
   `tests/fixtures/plans/` and load them from there):
   - `58b9853f2c47-opus.json`: exactly one `dep_coverage` error (T7 → server) and no other
     errors; warnings allowed.
   - `c7213225e46a-opus.json`: zero errors.
   - `58b9853f2c47-qwen.json`: errors include `dep_coverage` for the client task,
     `contract_interface` for `client` and `server`, and at least one `task_prompt_thin`.
   - `c7213225e46a-gptoss.json`: `task_module_unknown` for every task with module `none`.
   - a hand-written minimal plan that passes with zero violations (proves the checks are
     satisfiable), and the same plan with a transitive dependency chain T1→T2→T3 where only
     the transitive edge covers the module dependency (must pass `dep_coverage`).
   - `format_note` renders one line per violation with the ERROR/WARN tag.
   - engine, stub LLM: in `revise` mode a planner that returns a failing plan then a passing
     one yields `plan_meta.gate.passed == True`, two `rounds`, one `plan_gate_revise` log
     row, and the second call's prompt contains "AUTOMATED PLAN GATE" and the previous plan;
     a planner that keeps failing stops after `PLAN_GATE_MAX_ROUNDS` with `passed == False`
     and status `awaiting_plan`; in `warn` mode exactly one planner call is made and the
     violations appear in `plan_meta.warnings`; in `off` mode `plan_meta` has no `gate`.
   - `tests/test_plan_rules.py` still passes (the moved heuristics behave identically).
6. **Docs**: `.env.example` and `README.md`: the three vars, what each check means in one
   line each, and that the gate never fails a run — it revises (bounded) or warns.

## Out of scope

- No changes to the planner prompt text (`seed_prompts.py`), the plan schema, the human
  approval flow (`answer_plan`), or `_materialise_plan`.
- No bench changes (next task). No deploy, no commit, no `.env` edits. Never `pkill`; never
  touch ports 8400/8401, any VM or the Proxmox host; tests must not reach the network.

## Verification criteria

- `.venv/bin/python -c "import json,pipez.plangate as g; p=json.load(open('.odin/bench/plans/58b9853f2c47-opus.json')); v=g.check_plan(p); print([x['code']+':'+x['subject'] for x in v if x['severity']=='error'])"`
  prints exactly `['dep_coverage:T7']`.
- Same command on `58b9853f2c47-qwen.json` prints ≥ 4 errors including `dep_coverage` and
  `contract_interface`.
- `.venv/bin/pytest -q tests/test_plan_gate.py tests/test_plan_rules.py tests/test_local_tiers.py` passes.
- Full `.venv/bin/pytest -q` passes (known flaky `test_stop_kills_remote_runner_group` may be
  rerun alone).
- `grep -n "PLAN_GATE" pipez/config.py pipez/engine.py` shows the mode read in `_plan` only.

## Write summary

Last step, mandatory: `.odin/sessions/task-32-plan-gate-summary.md` — files changed, the
per-fixture violation lists the gate produces (codes + subjects), test counts before/after,
anything undone or uncertain.
