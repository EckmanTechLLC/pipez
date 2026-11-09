# pipez ↔ Odin models: diagnosis & fixes (2026-11-05)

Diagnosed from live Odin server logs after a pipez session found gpt-oss "unusable
(spec-decode fault + 30-min stalls)". Two separate problems, neither a model defect.

Odin endpoints (OpenAI-compatible, LAN/VPN):
- coder    http://192.168.50.17:8002/v1  model `qwen3.6-coder`  (Qwen3.6-35B-A3B, MTP, ~100 t/s)
- reasoner http://192.168.50.17:8004/v1  model `gpt-oss-120b`   (reasoning model, ~50 t/s, NO spec decode)
- embed    http://192.168.50.17:8001/v1  model `qwen3-embed`

## Problem 1 — gpt-oss "30-minute stall" = unbounded reasoning runaway (USAGE, not a bug)
- gpt-oss-120b is a REASONING model and has NO speculative decoding (the "spec-decode
  fault" did not come from here — see Problem 2).
- Server log showed one call generate **55,394 tokens @ ~29 t/s ≈ 32 min**, nearly
  filling its 65k context. It wasn't hung — it was generating an enormous reasoning
  trace because the client sent an unbounded/huge `max_tokens` and default reasoning.
- PROOF it's fine when bounded: same model with `max_tokens:200, reasoning_effort:"low"`
  answered in **1.9 s** (54 tokens).
- FIX (pipez side): on every gpt-oss call send a sane `max_tokens` cap AND
  `reasoning_effort` ("low" or "medium"). Reserve gpt-oss for genuinely hard
  reasoning/review; use the coder for normal work.

## Problem 2 — coder spec-decode faults + 500s = pipez sending oversized prompts (ROOT: client)
- The "speculative decoding fault" came from the CODER (:8002), which runs MTP.
  Logs: `E spec draft: llama_process[N] returned 1`, plus **178 memory-slot / 500 /
  exception events in 48 h**.
- They cluster around pipez sending **100k–131k-token prompts**. The clincher:
  `stop processing: n_tokens = 131071, truncated = 1` — prompts hit the 131,072 ctx
  ceiling and were **silently TRUNCATED** (you are losing context on those calls,
  degrading results even when it doesn't 500). Under that max-context pressure, MTP
  drafting across the server's slots throws draft errors and memory-slot 500s.
- FIX (pipez side, primary): stop stuffing whole repos/history into one giant prompt.
  Send FOCUSED context. The intended tool for this already exists on Odin:
  **MCP `search_codebases`** (launch `ssh etl@192.168.50.17 /home/etl/rag/run-mcp.sh`;
  tools `search_codebases(query, repo?, limit?)`, `list_repos()`) — retrieve the few
  relevant chunks per step instead of dumping 120k tokens. That removes both the
  truncation and the faults.
- FIX (server side, optional safety net, owned by the Work/infra session): harden the
  coder to degrade gracefully under max-context — tune `-np` (parallel slots) and/or
  drop MTP for the heavy-agent path (MTP is the piece that errors under pressure;
  costs some speed, kills the spec faults). Not yet applied — ask the infra session.

## Bottom line
- gpt-oss: not broken — bound `max_tokens` + set `reasoning_effort`.
- coder: fix by sending focused (RAG-retrieved) context instead of 120k-token prompts;
  that stops the silent truncation AND the spec/500 faults. Server hardening optional.
