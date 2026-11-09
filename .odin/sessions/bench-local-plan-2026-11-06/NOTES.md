# Plan-stage bench on Odin local tiers — 2026-11-05/06 (UTC)

Setup: task 29 tiers; `local` = qwen3.6-coder @ :8002 (thinking off), `local_reason` =
gpt-oss-120b @ :8004 (reasoning_effort=high). pipez sent `max_tokens=128000` (config.MAX_TOKENS)
and `PIPEZ_LOCAL_TIMEOUT=1800`. 5 stored runs, plan stage, $0 paid. Raw: bench-*.json, bench29.log.

| tier | ok | p50 s | out tok | tasks (Opus 8.2) | deploy-match | forbidden |
|---|---|---|---|---|---|---|
| local_reason | 2/5 | 7340 | 9.5k | 0.0 | 0% | 0 |
| local | 5/5 | 130 | 3.9k | 8.6 | 80% | 0 |

## gpt-oss-120b — result INVALID (uncapped call), not negative
Server logs (:8004) show five back-to-back single-slot tasks ending 23:07, 23:39, 00:10, 00:40,
01:12 UTC, each ~53–59k tokens total (prompt ~2.7k, rest generated), ~30 min each. These are
pipez's call + its 3 facade retries: client timed out at 1800 s, server kept generating to the
65k ctx, retry queued behind it. The two "ok" outputs were schema-valid but had zero modules
and zero tasks (19k chars of architecture prose). Fix = task 30 (per-tier max_tokens, no retry
after local timeout/truncation), then re-bench.

## qwen3.6-coder — form without substance
All 5 parsed; right task counts and module names; dependency edges; acceptance lists. But for
58b9853f2c47 (tic-tac-toe): contracts 180–620 chars vs Opus 1.1–2.7k; client contract is a file
list with no protocol; client task prompt 237 chars ("Handle WebSocket communication"); client
`depends_on: []` (same dropped edge that broke the real build); server "no automated tests due to
WebSocket complexity". It would have produced the broken UI faster and for free.

## Odin-side evidence vs odin-model-diagnosis.md
- Problem 1 (gpt-oss runaway): confirmed, pipez-caused (max_tokens 128000 + effort high).
- Problem 2 (coder 100–131k prompts attributed to pipez): NOT pipez. pipez's coder prompts were
  2,903 and 2,690 tokens (server + bench agree). ~123k-token prompts arrive every 12–20 min on
  slot 2 from 23:24 UTC through ≥07:29 (6 h after the bench ended); 131k truncations occurred
  11-04 13:45 → 11-05 19:38 (before pipez's first contact at 17:25 UTC 11-05) and 07:04 11-06
  (task 181524, the 123k client). llama-server logs no client IPs.
- Spec-decode exceptions 01:15/01:16/01:21 UTC on slots 3/2/1 followed `failed to find a memory
  slot for batch of size 10/16` bursts while slot 2 held a 123k prompt: pipez's small request
  was the victim (c6973d74ac0e local call, 705 s with retries). Server hardening (fewer slots /
  no MTP) would protect pipez; RAG for the planner prompt does not apply (already ~2.7k tokens).

## 2026-11-06 follow-up: task 30 bounds + probes (probe.log, probe2.log, bench30.log)
- Re-bench local_reason @ medium, max_tokens 16000: 0/5, every call truncated at 16k, 6–10 min each,
  no retry stacking (task 30 works).
- Probe on c7213225e46a plan prompt, json_schema grammar: low/16k → reasoning 216 chars, content 35,815
  chars, never closes the first string ("**.**\n\n---" repeated); medium/40k → reasoning 3,166 chars,
  content 179,586 chars (re-narrates its instructions). NOT reasoning runaway: llama.cpp json_schema
  grammar + gpt-oss degenerates inside long string fields.
- Same prompt WITHOUT grammar (schema pasted in prompt): 100 s, 2,891 tokens, valid plan, 8 tasks /
  5 modules; json_object mode: 92 s, 2,624 tokens, valid, 7 tasks / 6 modules. Contracts 200–320 chars
  (thin, like qwen). Fix for gpt-oss = per-tier structured-output mode (json_object or prompt-only) +
  client-side schema validation, not a bigger cap.

## 2026-11-06 task 31 re-bench: gpt-oss @ json_object, medium effort, 16k cap (bench31.log, bench-20261106-163200.json)
5/5 ok, p50 139 s, 5.8k out tokens, 10.2 tasks (+2.0 vs Opus), deploy-match 80%, forbidden 0.
Task-32 gate on the five plans (errors): 17, 13, 17, 13, 10 — dominated by task_prompt_thin (3–12 per
plan), plus dep_coverage, consumer_names_interface, contract_interface, task_module_unknown (tasks on
module "none"/"docs"). Contracts 150–890 chars (gpt-oss slightly fuller than qwen's 180–620).
Reference: Opus tic-tac-toe 1 error, Opus shortener 0; qwen 14 / 17.

## 2026-11-06 convergence bench: gate-driven revisions, 2 rounds (bench33.log, bench-20261106-180234.json)
| tier | ok | p50 s (all rounds) | out tok | gate-err final | ref-err | pass |
| local_reason gpt-oss json_object | 4/5 | 601 | 19.5k | 4.0 | 0.8 | 0/5 |
| local qwen | 5/5 | 295 | 12.3k | 8.8 | 0.6 | 0/5 |
Per-run error trajectories (round0 → r1 → r2):
 gpt-oss: 58b9 19→10 (r2 lost to a 503 "Loading model"); c7213 12→3→1; 60cd 13→1→3; c6973 15→3→2; 883909 r0 schema fail ('stack' missing).
 qwen:    883909 8→9→9; 58b9 17→8→5; c7213 17→14→14; 60cd 13 (r1 500 "Context size has been exceeded"); c6973 14→5→3.
Reading: gpt-oss converges fast (12–19 errors → 1–3 in two rounds; the residue is contract_interface on
tests/infra/docs-type modules and one dep edge — arguably gate false positives on non-code modules);
qwen converges unevenly (two runs stall at 9 and 14; the stall is task_prompt_thin — it will not write
>400-char prompts when told to). Nobody passed in 2 rounds. Cost: $0, but 5–12 min per plan.
Odin incidents during run: gpt-oss 503 Loading model (~17:40 UTC), coder 500 context exceeded on a 6.9k-token prompt.

## 2026-11-09 bench34: gpt-oss json_object, tuned gate (task 34), up to 3 rounds (bench34.log, bench34-20261109-145455.json)
5/5 ok, p50 846 s all rounds (12–16 min per plan), 23.7k out tokens, 11.6 tasks (+3.4 vs Opus), deploy-match 80%,
forbidden 0, final gate-err mean 0.2, ref-err 0.6, **pass 4/5 (80%)**, mean 2.0 rounds among passers.
Trajectories (r0 → r1 → r2 [→ r3]):
 883909 2→1→0 pass; 58b9 6→1→0 pass; c7213 4→2→0 pass (7 warnings left); c6973 12→4→0 pass;
 60cd 8→1→1→1 FAIL — stuck on contract_interface:ui ("contract of ui does not enumerate its public interface")
 for three rounds; gpt-oss will not enumerate a UI module's interface when told to.
Reading: after tuning, round-0 errors dropped from 10–19 to 2–12 (the tests/infra/docs residue is gone) and the
reasoner clears the gate in 2 rounds on 4/5 runs. No Odin incidents this run. Note: this bench predates task 35,
so the JSON holds only round-0 `output`; the passing final plans were not saved.
