# Task 01 summary

## Created
- /home/etl/projects/pipez: .gitignore, run.py, requirements.txt, .env.example, README.md, ui/index.html
- pipez/{__init__,config,db,engine,events,llm,seed_prompts,server}.py (copied from aimee-v2, renamed)
- .odin/project-index.md; git init done (nothing committed); .venv created

## Changes beyond rename
- config.py: MODEL/EFFORT replaced by MODEL_TIERS dicts + DEFAULT_TIER as specified.
- engine.py: _decide/_synthesize/verify/_render_artifacts resolve tier dicts (verify=deep, render=standard).
- llm.py: tier dicts threaded through decide/synthesize/verify/_call/_call_once/render_artifacts; effort from tier; non-anthropic provider raises LLMError (via new _resolve_tier helper); fallbacks for claude-opus-5 / claude-sonnet-5-5 / claude-fable prefixes. MockLLM accepts and ignores tier.
- Interpretation: ui placeholder "AIMEE proposes" -> "pipez proposes"; seed prompt "for AIMEE" -> "for pipez". Port 8321 -> 8400.
- requirements.txt: anthropic>=1.13, fastapi==0.143.1, uvicorn==0.54.0.

## Installed versions
anthropic 1.13.0, fastapi 0.143.1, uvicorn 0.54.0, pydantic 2.14.1

## Verification
1. import pipez.server: met (IMPORT_OK).
2. grep -rin aimee: met; only README.md line 3 ("Seeded from aimee-v2").
3. Mock run: met. POST returned {"run_id":"3c2c152274ce"}; /api/runs showed status "done" after ~20 s; tasks: root decompose(deep) -> 3 children decompose (deep/standard/light) -> 6 grandchildren complete, tiers populated on all non-root; /api/events began with a snapshot event. Server killed via fuser, /tmp/pipez-test* removed.
4. git status: new files untracked, no commits; .env, .venv, pipez.sqlite3 confirmed ignored via git check-ignore.
5. project-index.md: met, 66 lines, all 8 pipez/*.py files listed.

## Noticed in aimee-v2, not changed
- engine _decompose falls back to hard-coded "deep" for unknown tiers (left as is; not DEFAULT_TIER).
- Original README references ARCHITECTURE.md and reference/v13/ which were not copied.
- Original server uses deprecated @app.on_event("startup").
