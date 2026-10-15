# Task 01: Seed the pipez kernel from aimee-v2

## Objective
Create the pipez project by copying the aimee-v2 engine (a ~1,400-line Python
hierarchical task-decomposition engine) into `/home/etl/projects/pipez`, renaming
everything from `aimee` to `pipez`, updating the model tiers to current Claude
models with per-tier effort, and proving it boots and completes a run in mock
mode. This is a seed, not a redesign: behaviour must be identical to aimee-v2
except for the renames and the tier/effort change listed below. Later tasks add
a provider abstraction, preference store, intake stage, and sandboxed execution.

## Reference (read first)
- `/home/etl/projects/aimee-v2/ARCHITECTURE.md` — what the engine is and why
- `/home/etl/projects/aimee-v2/aimee/config.py` — env-driven config; note `MODEL_TIERS` and `EFFORT`
- `/home/etl/projects/aimee-v2/aimee/llm.py` — `_call_once` builds `output_config={"format":..., "effort": config.EFFORT}`; `_create` decides fallbacks by model prefix
- `/home/etl/projects/aimee-v2/aimee/engine.py` — `_decide` / `_synthesize` look up `config.MODEL_TIERS[tier]` and pass a model string to `llm.decide/synthesize`
- `/home/etl/projects/aimee-v2/aimee/{db,events,server,seed_prompts}.py`, `run.py`, `requirements.txt`, `.gitignore`, `ui/index.html`
- Do NOT modify anything under `/home/etl/projects/aimee-v2`.

## Deliverables
1. **Repository.** `git init` in `/home/etl/projects/pipez` (do not commit). `.gitignore` containing at least:
   `.env`, `.venv/`, `pipez.sqlite3*`, `pipez.log`, `__pycache__/`, `artifacts/`, `.odin/index-cache.json`.
2. **Package copy + rename.** Copy `aimee-v2/aimee/*.py` → `pipez/pipez/`, `aimee-v2/ui/index.html` → `pipez/ui/index.html`, `run.py`, `requirements.txt`. Then rename consistently:
   - Python package `aimee` → `pipez` (imports, `uvicorn.run("pipez.server:app", ...)`, logger names `pipez.engine` / `pipez.llm`).
   - Env prefix `AIMEE_` → `PIPEZ_` everywhere (config.py and any docs).
   - Default DB `pipez.sqlite3`, default artifacts dir `./artifacts`, default port **8400** (`PIPEZ_PORT`), default host `127.0.0.1`.
   - UI `<title>` and header text `PIPEZ`; FastAPI `title="PIPEZ"`.
   - In `seed_prompts.py`, replace the product name in prompt text ("inside AIMEE" etc.) with "inside pipez". Leave prompt semantics unchanged.
   - After this step `grep -ri aimee pipez/ ui/ run.py README.md` must return only the single lineage sentence in README.md (deliverable 5).
3. **Tier → {provider, model, effort}.** In `config.py` replace `MODEL` / `MODEL_TIERS` / `EFFORT` with:
   ```python
   MODEL_TIERS = {
       "deep":     {"provider": "anthropic", "model": os.environ.get("PIPEZ_MODEL_DEEP", "claude-opus-5-5"),     "effort": os.environ.get("PIPEZ_EFFORT_DEEP", "high")},
       "standard": {"provider": "anthropic", "model": os.environ.get("PIPEZ_MODEL_STANDARD", "claude-sonnet-5-5"), "effort": os.environ.get("PIPEZ_EFFORT_STANDARD", "medium")},
       "light":    {"provider": "anthropic", "model": os.environ.get("PIPEZ_MODEL_LIGHT", "claude-haiku-5-5"),    "effort": os.environ.get("PIPEZ_EFFORT_LIGHT", "low")},
   }
   DEFAULT_TIER = "deep"
   ```
   Remove the global `EFFORT`. Thread the change through:
   - `engine.py`: `_decide`, `_synthesize`, `verify`, `_render_artifacts` resolve a tier dict (`config.MODEL_TIERS.get(task.get("tier") or DEFAULT_TIER, config.MODEL_TIERS[DEFAULT_TIER])`) and pass it to the LLM layer. Verify stays `deep`; render stays `standard`.
   - `llm.py`: `decide/synthesize/verify/_call/_call_once/render_artifacts` accept a `tier: dict` (not a model string) and use `tier["model"]` and `output_config["effort"] = tier["effort"]`. `usage["model"]` still reports the model string. The `provider` key is read but only `"anthropic"` is supported in this task — raise `LLMError` for anything else.
   - `_create`: fallbacks (`betas=["server-side-fallback-2026-07-01"], fallbacks="default"`) apply when the model starts with `claude-opus-5`, `claude-sonnet-5-5`, or `claude-fable`; never for haiku. Keep the existing "disable on BadRequestError" behaviour.
   - `MockLLM` methods accept the same `tier` argument and ignore it.
   - Decision schema `tier` enum stays `["deep","standard","light"]`.
   Leave the `web_search_20260209` / `web_fetch_20260209` / `code_execution_20260120` tool types as they are.
4. **Environment.** `python3 -m venv .venv`; `.venv/bin/pip install -r requirements.txt` with `requirements.txt` pinned to the versions actually installed (`anthropic>=1.13`, `fastapi`, `uvicorn`). Create `.env.example` listing every `PIPEZ_*` variable with its default, plus `ANTHROPIC_API_KEY=` and two commented placeholders `# PIPEZ_LOCAL_BASE_URL=` / `# PIPEZ_LOCAL_MODEL=` (used by task 02).
5. **README.md** (short, replaces the aimee one): what pipez is (one paragraph: decision-first, end-to-end software builder on a hierarchical task engine; seeded from aimee-v2 — that is the one permitted "aimee" mention), run instructions, env var table, `fuser -k 8400/tcp` to stop.
6. **`.odin/project-index.md`**: one section per module in `pipez/` listing classes and public functions with one-line descriptions, plus the HTTP routes in `server.py`. Keep it under 120 lines.

## Out of scope
- Any provider other than Anthropic; the `local` tier; vLLM (task 02).
- Preference store, intake stage, decision schema changes, UI changes beyond the rename (tasks 03–05).
- Proxmox, Docker, sandboxing (task 07).
- Changing engine behaviour, prompts (beyond the name), schema, or DB layout.
- Committing to git.
- Editing anything in `/home/etl/projects/aimee-v2`.

## Verification criteria
Run these yourself and record the output in the summary:
1. `cd /home/etl/projects/pipez && .venv/bin/python -c "import pipez.server"` succeeds.
2. `grep -rin aimee pipez/ ui/ run.py README.md .env.example` → only the README lineage line.
3. `PIPEZ_MOCK=1 PIPEZ_DB=/tmp/pipez-test.sqlite3 PIPEZ_ARTIFACTS_DIR=/tmp/pipez-test-artifacts .venv/bin/python run.py &` starts on 8400. Then:
   - `curl -s -X POST localhost:8400/api/requests -H 'Content-Type: application/json' -d '{"request":"smoke test"}'` returns `{"run_id": ...}`.
   - Within ~20 s `curl -s localhost:8400/api/runs` shows that run with `status: "done"`.
   - `curl -s localhost:8400/api/runs/<run_id>/tasks` shows a tree: root `decompose` → 3 children → each 2 grandchildren `complete` (the MockLLM shape), with `tier` populated on every non-root task.
   - `curl -sN --max-time 3 localhost:8400/api/events | head -c 400` shows a `snapshot` event.
   - Kill the server (`fuser -k 8400/tcp`) and delete the two `/tmp/pipez-test*` paths.
4. `git status` shows the new files untracked and nothing committed; `.env`, `.venv/`, `*.sqlite3` are ignored.
5. `.odin/project-index.md` exists and every file in `pipez/` appears in it.

## Write summary
MANDATORY, last step: `.odin/sessions/task-01-seed-kernel-summary.md` with: files created, exact package versions installed, the verification command outputs (trimmed), and anything in aimee-v2 that looked wrong or stale that you did NOT change (flag it, do not fix it).
