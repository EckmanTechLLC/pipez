# Task 02: Provider abstraction, `local` tier, per-subtask effort, escalation

## Objective
Split the LLM layer into providers so a tier can be backed by Anthropic or by an
OpenAI-compatible endpoint (vLLM serving qwen3 on a remote box). Add a `local`
tier that exists only when `PIPEZ_LOCAL_BASE_URL` is configured, let the
decomposing parent override `effort` per subtask, and make the engine escalate
to the next tier up when a tier's call fails. The vLLM box is NOT reachable
during this task: verify the local provider against an `httpx.MockTransport`
stub, never against a live server. Engine behaviour is otherwise unchanged.

## Reference (read first)
- `.odin/project-index.md` — module map
- `pipez/config.py` — `MODEL_TIERS`, `DEFAULT_TIER` (task 01 shape)
- `pipez/llm.py` — `LLM._resolve_tier`, `_create`, `_call` (slow retries), `_call_once` (builds the Anthropic request, pause_turn loop, refusal/max_tokens handling), `decide/synthesize/verify/render_artifacts`, `MockLLM`, `DECISION_SCHEMA`
- `pipez/engine.py` — `_decide`, `_decompose` (creates children, reads `sub["tier"]`), `_synthesize`, `verify`, `_render_artifacts`, `db.log` usage
- `pipez/db.py` — `TASK_COLUMNS`, `MIGRATIONS` (append-only ALTERs that swallow OperationalError), `create_task`
- `pipez/seed_prompts.py` — `system_default` paragraph "Give each subtask a `tier`"
- `.env.example`, `README.md`

## Deliverables
1. **`pipez/providers.py`** (new). A `Provider` base with
   `async def complete(self, system: str, prompt: str, schema: dict, tier: dict, *, fetch: bool = False) -> tuple[dict, dict]`
   returning `(parsed_json, usage)` where `usage = {"input_tokens", "output_tokens", "model"}`. Two implementations:
   - `AnthropicProvider`: move the current `_create` + `_call_once` body here unchanged (streaming, fallbacks gate, web_search/web_fetch tools, pause_turn loop, refusal → `LLMError`, max_tokens → `LLMError("... truncated ...")`). Also move `render_artifacts` here (`async def render_artifacts(system, prompt, dest_dir, tier)`), Anthropic-only.
   - `OpenAICompatProvider`: `httpx.AsyncClient` (httpx is already installed as an anthropic dependency; do not add `openai`). `POST {base_url}/v1/chat/completions` with `model=tier["model"]`, `messages=[{"role":"system",...},{"role":"user",...}]`, `max_tokens=config.MAX_TOKENS`, `temperature=config.LOCAL_TEMPERATURE`, `response_format={"type":"json_schema","json_schema":{"name":"out","schema":schema,"strict":True}}`, and `chat_template_kwargs={"enable_thinking": config.LOCAL_THINKING}` in the body (vLLM reads it). Optional `Authorization: Bearer {PIPEZ_LOCAL_API_KEY}`. Timeout `config.LOCAL_TIMEOUT`. Parse `choices[0].message.content`; strip a leading `<think>...</think>` block if present; `json.loads`. Usage from `usage.prompt_tokens/completion_tokens`, model from `response.model`. `finish_reason == "length"` → `LLMError("response truncated at max_tokens")`. HTTP 5xx and transport errors raise `ProviderTransientError(LLMError)`; 4xx raise `LLMError` with the body text. `effort`, `fetch`, and web tools are ignored. Expose `async def healthy(self) -> bool` doing `GET {base_url}/v1/models` with a 5 s timeout.
   `LLMError` moves to `providers.py`; `llm.py` re-exports it so existing imports keep working.
2. **Config + facade.** `config.py`: add
   `LOCAL_BASE_URL`, `LOCAL_MODEL`, `LOCAL_API_KEY` (default empty), `LOCAL_TIMEOUT` (default 600), `LOCAL_TEMPERATURE` (default 0.2), `LOCAL_THINKING` (default `0` → False), `ESCALATE_ON_FAILURE` (`PIPEZ_ESCALATE`, default 1). Add a `"local"` entry to `MODEL_TIERS` **only when** `LOCAL_BASE_URL` is non-empty: `{"provider": "openai_compat", "model": LOCAL_MODEL, "effort": "low", "base_url": LOCAL_BASE_URL}`. Add `TIER_ORDER = ["local", "light", "standard", "deep"]` (cheapest first; entries absent from `MODEL_TIERS` are skipped) and a helper `next_tier_up(name) -> str | None`.
   `llm.py`: `LLM` becomes a facade owning one provider instance per provider name (`{"anthropic": AnthropicProvider(), "openai_compat": OpenAICompatProvider()}`), keeps `_call`'s slow-retry loop (now catching `ProviderTransientError` plus the existing Anthropic exception classes and `"truncated"` errors), and dispatches `provider.complete(...)` by `tier["provider"]`. `verify` and `render_artifacts` force an Anthropic tier (`deep` / `standard`) and raise `LLMError` if that tier is not Anthropic. `decide/synthesize/verify` signatures are unchanged from task 01.
3. **Per-subtask effort.** `DECISION_SCHEMA` becomes a function `decision_schema()` so the subtask `tier` enum is `list(config.MODEL_TIERS)` at call time, and each subtask gains an optional `"effort": {"type":"string","enum":["low","medium","high","xhigh","max"]}` (NOT in `required`). `db.py`: append `ALTER TABLE tasks ADD COLUMN effort TEXT` to `MIGRATIONS`, add `effort` to `TASK_COLUMNS`, `create_task(..., effort=None)`. `engine._decompose` stores `sub.get("effort")` when it is one of the five values. Effort resolution happens in the engine, not the LLM layer: where `_decide`/`_synthesize` resolve the tier dict, pass `dict(tier, effort=task["effort"])` when `task["effort"]` is set. `MockLLM.decide` sets `"effort": "low"` on exactly one of its subtasks so the column path is exercised.
4. **Escalation + health.** In `engine.py`, wrap the two LLM calls in `_decide` and `_synthesize`: on `LLMError` after `_call`'s retries, if `config.ESCALATE_ON_FAILURE` and `next_tier_up(current)` exists, `db.log(task_id, "escalated", {"from", "to", "reason"})`, `_update(task_id, tier=new)`, and retry with the new tier; stop when there is no higher tier and re-raise. Never escalate past `deep`. Separately, at `Engine.__init__` if a `local` tier exists, schedule one `healthy()` probe on the running loop (or run it lazily on first local use); if it fails, log a single warning and set `self._local_down = True`, after which any task whose tier is `local` resolves to `light` (log `escalated` with reason `"local provider unavailable"`). The usage `model` recorded on the task is whatever model actually answered.
5. **Prompts, env, docs.** `seed_prompts.system_default`: in the tier paragraph add `'local' (when it appears in the allowed tiers) for trivial mechanical work where latency does not matter;` and a sentence: `You may also set 'effort' on a subtask (low..max) only when the tier's default is clearly wrong for that specific piece of work.` Update `.env.example` (all new `PIPEZ_LOCAL_*` and `PIPEZ_ESCALATE` with defaults; URL/model stay commented-out placeholders), the README env table, and `.odin/project-index.md` (add `providers.py`, update `llm.py`/`config.py`/`engine.py` entries).
6. **Tests** (new `tests/` directory, `requirements-dev.txt` with `pytest` and `pytest-asyncio`, installed into `.venv`). Using `httpx.MockTransport` (no sockets):
   - request body shape: model, messages, `response_format.json_schema.schema` equals the schema passed, `chat_template_kwargs.enable_thinking` is False by default;
   - `<think>…</think>` prefix is stripped before JSON parse; usage and model are read from the response;
   - `finish_reason: "length"` → `LLMError` containing "truncated";
   - HTTP 503 → `ProviderTransientError`;
   - `healthy()` true on 200, false on connect error;
   - escalation: with `PIPEZ_LOCAL_BASE_URL` set and a provider stub whose `complete` always raises `LLMError`, an engine `_decide` on a `local`-tier task ends up calling the `light` tier and `task_log` contains an `escalated` row (use a temp SQLite path; monkeypatch `config.RETRY_BASE_DELAY = 0` and `config.TASK_RETRIES = 0` so the test is fast);
   - `decision_schema()` tier enum contains `local` only when the env var is set (test both by monkeypatching `config.MODEL_TIERS`).

## Out of scope
- Any live call to the vLLM box or to Anthropic.
- Preference store, intake stage, UI changes (tasks 03–05); Proxmox/sandboxing (07).
- Running `verify` or `render_artifacts` on a non-Anthropic provider.
- Changing prompt semantics beyond the two sentences in deliverable 5.
- Committing to git.

## Verification criteria
Run these and record output in the summary:
1. `.venv/bin/python -m pytest -q tests/` passes.
2. `grep -n "import anthropic\|from anthropic" pipez/*.py` → only `providers.py`.
3. `PIPEZ_MOCK=1 PIPEZ_DB=/tmp/pz02a.sqlite3 PIPEZ_ARTIFACTS_DIR=/tmp/pz02a-art .venv/bin/python run.py` on 8400: POST a request, run reaches `done`, `/api/runs/<id>/tasks` shows one task with `effort: "low"` and the rest `null`. Stop the server, delete the temp paths.
4. Same as 3 but with `PIPEZ_LOCAL_BASE_URL=http://127.0.0.1:9 PIPEZ_LOCAL_MODEL=qwen3`: server starts, logs exactly one warning about the local provider, run still reaches `done` (MockLLM is in use, so this proves startup/health wiring, not escalation). Stop, clean up.
5. `.venv/bin/python -c "from pipez import config; from pipez.llm import decision_schema; print(decision_schema()['properties']['subtasks']['items']['properties']['tier']['enum'])"` prints `['deep', 'standard', 'light']` without the env var and includes `'local'` with `PIPEZ_LOCAL_BASE_URL=http://x PIPEZ_LOCAL_MODEL=m` set.
6. `.odin/project-index.md` lists `providers.py` and `tests/`.

## Write summary
MANDATORY, last step: `.odin/sessions/task-02-providers-local-tier-summary.md` with: files created/changed, dev packages installed with versions, trimmed verification output, and any ambiguity you resolved (state what you chose and why).
