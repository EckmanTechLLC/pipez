# Task 17 — Local tier: safe wiring + benchmark harness

## Objective
A vLLM 0.19.2 endpoint serves `qwen3.6` (131k ctx) at `http://192.168.50.17:8000` (OpenAI
compatible; `/v1/models`, `/v1/chat/completions`, `response_format json_schema` works,
`chat_template_kwargs.enable_thinking` honoured). Measured ~9 output tokens/s. Make enabling the
`local` tier safe (code leaves must never get it; long outputs must not time out silently), and
build a benchmark script that replays the real intake and brief prompts of the stored runs
through `local`, `light` and `standard`, reporting schema validity, agreement with the stored
(Opus/Sonnet) answers, latency and estimated cost — so the human can decide where `local` is
allowed. Do NOT enable the tier in any `.env` yourself (Odin Prime does that on the VM).

## Reference
- `pipez/config.py`: `LOCAL_*`, `MODEL_TIERS["local"]` (conditional), `TIER_ORDER`,
  `next_tier_up`, `MAX_TOKENS`, `PRICES_PER_MTOK`/`estimate_cost` (task 16).
- `pipez/providers.py`: `OpenAICompatProvider.complete` (~205–250: max_tokens, timeout,
  truncation → `LLMError("response truncated at max_tokens")`), `healthy()`.
- `pipez/llm.py`: `LLM.intake/brief/plan` signatures, `INTAKE_SCHEMA`, `BRIEF_SCHEMA`,
  `make_llm`, `MockLLM`.
- `pipez/engine.py`: `_intake` (~249–290: system/prompt assembly incl.
  `_known_keys_section`), `_brief_and_start` (~373–420: `_decision_table`, prompt parts),
  `_run_llm_call`, `_materialise_plan` tier validation (~620–630), `_llm_call` local-down
  fallback (~120–170), `_probe_local`.
- `pipez/db.py`: runs columns `request`, `decisions`, `answers`, `brief`, `intake_meta`,
  `plan`, `project_id`; `get_runs`.
- `tests/test_escalation.py` (local tier tests, fake provider patterns), `tests/test_plan.py`.
- `.env.example` (PIPEZ_LOCAL_* block), `README.md`.

## Deliverables
1. **Safety.** (a) `_materialise_plan`: a plan task with `kind=code` and `tier=local` → `light`
   (log `tier_coerced` with reason "code tasks run on the Agent SDK"); same coercion anywhere
   a code task's tier is resolved to a model (reviewer/fixer use fixed tiers already — assert).
   (b) `config.LOCAL_ALLOWED_STAGES` (`PIPEZ_LOCAL_STAGES`, comma list, default empty) — the
   stages whose run-level/decision calls may *start* on `local` (names: `intake`, `brief`,
   `plan`, `decide`, `synthesize`); the planner/decide prompts show `'local'` as allowed only
   when the relevant stage is listed; with an empty list `local` is never auto-selected but
   still usable by explicit task tier override and by the benchmark. (c) Provider: when the
   endpoint answers with `finish_reason=length`, the existing truncation error stands; add
   the elapsed seconds and output tokens to the error text so logs show the speed.
2. **Prompt builders factored out** (pure refactor, behaviour identical): `_intake_prompt(run)
   -> (system, prompt)` and `_brief_prompt(run, sources) -> (system, prompt)` used by `_intake`
   and `_brief_and_start`, importable by the benchmark.
3. **Benchmark** `scripts/bench_local.py` (new `scripts/` dir): CLI
   `--db <path> --runs <id,...|all> --stages intake,brief --tiers local,light,standard
   --out <dir>`; for each stored run (needs `request`; for brief needs `decisions`+`answers`)
   rebuild the prompt via the factored builders (construct `Engine` with the real DB read-only
   — never submit or mutate runs; if a builder needs a `Run` dict, use `db.get_run`), call
   `LLM.intake/brief` per tier with the real providers, record: ok/error, seconds, in/out
   tokens, `estimate_cost`, schema-valid (the provider already validates — record exceptions),
   and agreement metrics vs the stored answer: intake → Jaccard of decision keys and the share
   of overlapping keys whose proposed default equals the stored one; brief → length ratio and
   count of the stored brief's `## ` headings present. Write `<out>/bench-<ts>.json` and print a
   markdown table (stage × tier: n, ok%, p50 s, mean $, key-Jaccard, default-agree%). Requires
   `ANTHROPIC_API_KEY` and `PIPEZ_LOCAL_BASE_URL/MODEL` in env; refuse to run with the mock LLM.
   Honour `--max-per-stage N` (default 2) to bound spend; print the estimated spend before
   calling and require `--yes` to proceed.
4. Docs: `.env.example` gains `PIPEZ_LOCAL_STAGES=`; README gets a "Local tier" paragraph with
   the measured 9 tok/s caveat and the bench usage.
5. Tests (mock, no network): (a) code task `tier=local` → `light` + log; (b) with
   `LOCAL_ALLOWED_STAGES` empty the planner prompt does not list `'local'`, with `plan` listed
   it does; (c) `_intake_prompt`/`_brief_prompt` return the same strings the old inline code
   produced (snapshot via a mock run); (d) bench: a dry run (`--dry-run`) against a temp DB
   with two runs prints the plan and exits 0 without network.

## Out of scope
- Running the benchmark for real (Odin Prime does, after the user enables the tier on the
  VM); changing which model serves on .17; speculative decoding/quantisation; prompt wording
  beyond the `'local'` gating; committing; touching `.env` or the live VM.

## Verification criteria
- `grep -n "LOCAL_ALLOWED_STAGES\|tier_coerced" pipez/config.py pipez/engine.py`; `ls scripts/bench_local.py`.
- `.venv/bin/python scripts/bench_local.py --help` exits 0.
- `.venv/bin/python -m pytest -q` passes (currently 139; expect ≥144). Suite ~4.5 min — Bash
  timeout ≥ 560000 ms; never conclude it hangs before 8 min.
- No commits, no `pkill`, nothing on 8400/8401, no new deps, no network calls from tests.

## Write summary
`.odin/sessions/task-17-local-tier-bench-summary.md` — mandatory last step.
