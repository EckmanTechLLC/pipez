# Task 02 summary

## Created
- pipez/providers.py: LLMError, ProviderTransientError, is_transient(), Provider, AnthropicProvider (old _create/_call_once moved as complete(); render_artifacts moved), OpenAICompatProvider (+ healthy()).
- tests/{__init__,conftest,test_providers,test_escalation}.py, pytest.ini (asyncio_mode=auto), requirements-dev.txt.

## Changed
- pipez/config.py: LOCAL_* settings, conditional `local` tier, ESCALATE_ON_FAILURE, TIER_ORDER, next_tier_up().
- pipez/llm.py: LLM is now a facade (providers dict, optional `providers=` ctor arg for tests); decision_schema() replaces DECISION_SCHEMA (optional subtask effort, live tier enum); verify/render_artifacts require an Anthropic tier; MockLLM sets effort "low" on subtask 0 of the depth-0 decision only.
- pipez/db.py: effort column migration, TASK_COLUMNS, create_task(effort=).
- pipez/engine.py: _llm_call (tier + effort resolution, local-down fallback, escalation with `escalated` log rows); _decide/_synthesize use it; _decompose stores validated effort; _probe_local scheduled in __init__ when a local tier exists.
- pipez/seed_prompts.py (two sentences), .env.example, README env table, .odin/project-index.md.

## Dev packages installed in .venv
pytest 9.1.1, pytest-asyncio 1.4.0

## Verification
1. pytest: 7 passed.
2. grep: only pipez/providers.py (lines 33, 55, lazy imports).
3. Mock run on 8400: status done; effort counts {None: 9, 'low': 1}; no warnings. Server stopped, temp files removed.
4. With PIPEZ_LOCAL_BASE_URL=http://127.0.0.1:9: status done; exactly one WARNING ("local provider at http://127.0.0.1:9 is unavailable..."). Stopped, cleaned.
5. Enum prints ['deep', 'standard', 'light'] without env; ['deep', 'standard', 'light', 'local'] with env set.
6. project-index.md lists providers.py and tests/: met.

## Ambiguities / decisions
- httpx: the installed anthropic 1.13 depends on `httpx2`, not `httpx` (plain `httpx` is not in the venv). providers.py and tests use `import httpx2 as httpx` (same API, MockTransport included). No new dependency added.
- llm.py must not import anthropic (criterion 2), so transient classification of Anthropic exceptions lives in providers.is_transient(), used by LLM._call.
- Health probe uses llm.providers["openai_compat"] if present, else a fresh OpenAICompatProvider (needed in mock mode, where MockLLM has no providers).
- _llm_call re-reads the task from the DB so escalated tier / effort survive from _decide to _synthesize.
- Escalation test sets stub healthy()=True and calls _probe_local so the escalation (reason "boom"), not the local-down path, is what is exercised.
- Existing databases keep old seeded system_default prompt (seed only inserts when missing); new sentences apply to fresh DBs.
- .env.example already contained commented LOCAL_BASE_URL/MODEL placeholders; kept them commented.
