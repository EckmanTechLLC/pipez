# Task 18 — Configurable intake tier + bench against a reference tier on synthetic requests

## Objective
Make the intake discovery tier configurable (`PIPEZ_INTAKE_TIER`, default stays `deep`)
and extend `scripts/bench_local.py` so it can benchmark intake on *new* request texts
(not only stored runs) by comparing every tier's output against a reference tier's
output produced in the same bench run. This is the evidence needed before flipping
intake from Opus to Sonnet. Do not change the default tier in this task.

## Reference (read first)
- `pipez/config.py` — `MODEL_TIERS`, `local_allowed`, `VERIFY_TIER` (pattern for a
  stage-tier env var, line ~202).
- `pipez/engine.py` — `_intake_prompt` (~249), `_intake` (~258; hard-codes `"deep"`
  at ~266), `_run_llm_call`.
- `scripts/bench_local.py` — whole file (~260 lines): `select_runs`, `build_prompt`,
  `agreement_intake`, `one_call`, `table`, `main`.
- `tests/test_local_bench.py` — `_stored_run`, `test_prompt_builders_match_old_inline`,
  `test_bench_dry_run` (patterns to extend).
- `tests/conftest.py` — mock LLM is forced in tests; bench refuses to run with
  `config.MOCK`, so bench tests exercise `--dry-run` and the pure helper functions.

## Deliverables
1. `pipez/config.py`: `INTAKE_TIER = os.environ.get("PIPEZ_INTAKE_TIER", "deep")`,
   validated the same way as other tier names (fall back to `"deep"` with no
   exception if the value is not in `MODEL_TIERS`; do the check lazily or after
   `MODEL_TIERS` is final, since `local` is added later in the file).
   `pipez/engine.py` `_intake`: use `config.INTAKE_TIER` instead of the literal
   `"deep"` (the `local_allowed("intake")` branch stays first). `.env.example` and
   `README.md`: document `PIPEZ_INTAKE_TIER` in one line each next to
   `PIPEZ_VERIFY_TIER`.
2. `scripts/bench_local.py`:
   - `--request-files a.md,b.md` (or a directory: every `*.md` in it). Each file
     becomes a synthetic run dict `{"id": "req:<basename-without-ext>", "request":
     <file text>, "context": "", "project_id": <value of new flag --project-id,
     default "bench">, "decisions": None, "answers": None, "brief": None}`.
     Synthetic runs are added to the intake job list only (brief needs stored
     decisions/answers). `--db` stays required (prompts + known keys come from it).
   - `--reference-tier deep`: when set, for each job the reference tier is called
     first; every other tier's `agreement_intake` is computed against the reference
     output (same key-Jaccard / default-agree metrics) instead of the stored run
     decisions. Stored runs without `--reference-tier` behave exactly as today.
     The reference tier's own record gets `agreement: "reference"` and no
     Jaccard/agree values. If the reference call fails, the other tiers of that job
     are still called and recorded with agreement fields `None`.
   - Add a `locked_agree` metric in `agreement_intake`: Jaccard of the
     `request_values` key sets (new vs reference/stored). Stored runs: use the keys
     of stored decisions with `locked` true as the reference set.
   - `table()` shows a `locked-J` column for intake; the JSON records carry
     `reference_tier` and `request_file` where applicable.
   - `estimate()` and the `plan:` lines include synthetic jobs.
3. Four synthetic requests in `.odin/bench/requests/` (each 6–12 lines, written as
   a user would, each stating 2–4 explicit decisions that should become locked
   `request_values`, each on a different stack/domain):
   `cli-tool.md` (a Python CLI that deduplicates photos by perceptual hash, output
   JSON, no third-party services), `node-api.md` (a Node/Express URL shortener with
   SQLite, Docker image, rate limiting), `static-site.md` (a static markdown blog
   generator with RSS, deploy as plain files, no JS framework), `go-service.md`
   (a Go webhook relay with retry/backoff, Prometheus metrics, YAML config).
4. Tests in `tests/test_local_bench.py`:
   - `config.INTAKE_TIER` defaults to `deep`; invalid env value falls back to `deep`
     (reload-free: test the validation helper or monkeypatch and call the function).
   - `_intake` uses `config.INTAKE_TIER` (monkeypatch `INTAKE_TIER="standard"`,
     capture the tier passed to `llm.intake`).
   - Bench: `--request-files` with a temp dir of two `.md` files + `--dry-run`
     prints two `req:` plan lines and no brief job for them.
   - `agreement_intake` against a reference output (pure function) returns the
     expected Jaccard/agree/locked values on a small fixture.
5. Full suite passes: `python -m pytest -q` (takes ~4–5 minutes; use a timeout of at
   least 560 000 ms or run with nohup and poll the log — do not assume a hang before
   8 minutes). Flaky-looking failures must be rerun, not ignored.

## Out of scope
- Changing the default intake tier, prices, or any other stage's tier.
- Running the benchmark against real models (Odin Prime does that afterwards).
- Any change to UI, DB schema, prefs, or the runner.
- Never run `pkill`/`killall`; never touch ports 8400/8401; never touch Proxmox.

## Verification criteria
- `grep -n INTAKE_TIER pipez/config.py pipez/engine.py` shows the env var defined
  and used in `_intake`; `"deep"` literal is gone from `_intake`.
- `python scripts/bench_local.py --db pipez.sqlite3 --stages intake --tiers
  deep,standard --reference-tier deep --request-files .odin/bench/requests
  --max-per-stage 0 --dry-run` (set `PIPEZ_MOCK` unset; `ANTHROPIC_API_KEY` not
  needed for dry-run) prints 4 `req:` plan lines and an estimate, exit 0.
- `python -m pytest -q` green.

## Write summary
`.odin/sessions/task-18-intake-tier-bench-summary.md`: files changed, test count,
exact dry-run command output (first lines), anything deferred.
