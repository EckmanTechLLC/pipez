# pipez — the recursive decomposition idea (thread summary)

> Summary of the idea/thread as of 2026-11-05. Parts of the original brief were
> flagged by Matt as possibly wrong or unnecessary — treat this as a starting
> point to refine, not settled design. No architecture decisions are approved here.

## Core idea
Reduce dependence on expensive frontier models by making capability come from the
**organization and control of the work**, not from every model understanding and
solving the whole project. A local model **recursively decomposes** a task
(one → many → many → many) through as many levels as needed; only the final,
sufficiently-specific leaf tasks go to a capable coding model. Results return
upward, each parent coordinating and checking its children against its requirements.

**Analogy:** an engine design broken into operations performed by individual
mechanics — hold a bolt, position a tool, tighten it, confirm the spec. Coordination
lets narrowly-capable workers assemble the complete engine.

## Matt's framing / philosophy
- Matt (systems & architecture engineer) owns the architecture and engineering
  decisions, optionally developing the initial design with a frontier model.
- That design + requirements + constraints + coding instructions become the
  **authoritative starting point**.
- Capability is an emergent property of structure, not of any single model's
  intelligence. (Mirrors the Odin finding: a 35B local model became *trustworthy*
  via harness discipline — gates, verification, scoping — not raw model power.)

## Intended architecture
- A local model recursively decomposes; each child undergoes the same decide-then-
  decompose-or-execute process until it is a specific enough implementation task.
- Models operate through **restricted tools with defined parameters**; software
  controls the permitted operations and gates (not the model's discretion).
- Missing information is reported **through a tool** and routed for clarification —
  never guessed.
- Final leaf tasks go to an appropriate coding model (Haiku / Sonnet / Opus / a
  local model — chosen per task).
- Results propagate upward; each parent coordinates and verifies its children.

## Goals & preferences
- Reduce paid frontier-model usage and repeated correction.
- Prioritize **correctness and cost over elapsed time**.
- Explore **architectural** solutions before assuming more powerful hardware/models.
- Keep requirements explicit; supply tightly-scoped coding instructions.
- Do NOT assume frontier models must perform the decomposition.
- Do NOT introduce architecture decisions on Matt's behalf.

## What pipez already implements (verified at commit 7fa303c)
- Recursive decomposition: `_execute` → `_decide` → `_decompose`, children return
  through `_execute`.
- Stores task hierarchy and state, schedules sibling dependencies, synthesizes
  child results upward.
- Approved coding-plan path: `_materialise_plan` creates an integrator root and
  `kind="code"` children; `_execute` routes those directly to `_execute_code_task`,
  bypassing the decomposition decision.
- Coding execution: worktrees, test execution, merging, integration, retries,
  human-question handling.
- An OpenAI-compatible local provider exists.
- Recursion can truncate children at configured limits and remove invalid
  dependency edges.
(These are implementation observations, not agreed changes.)

## What remains unestablished (open questions)
- Whether the arrangement is actually cheaper / more reliable for Matt's real projects.
- Which local model and configuration are sufficient.
- The appropriate decomposition depth and task size.
- What each node receives, what its tools permit, and what qualifies a task for
  execution vs further decomposition.
- How dependencies, constraints, clarification, and completion propagate between levels.
- Model assignments and evaluation criteria.
- Whether any existing framework already implements this exact architecture
  (cited ones share components but weren't established as equivalent).

## Updated local-execution context (changed since the original brief)
The brief's "Qwen via vLLM at ~10–15 tok/s" is **obsolete**. Odin (192.168.50.17)
was rebuilt (see the Work/infra session):
- Coder: `http://192.168.50.17:8002/v1` — Qwen3.6-35B-A3B, ~100 tok/s, OpenAI-compatible.
- Reasoner: `http://192.168.50.17:8004/v1` — gpt-oss-120b (deeper, ~50 tok/s).
- Embeddings: `http://192.168.50.17:8001/v1`; plus a code-RAG index + MCP
  `search_codebases` tool over Matt's repos (pipez included).
This materially helps the idea: the recursion/decomposition layers can run on the
**fast local coder** for cheap, reserving frontier models (or gpt-oss) only for the
hardest leaves — exactly the cost-reduction the thread is chasing.

## Suggested next steps
1. Use pipez as the concrete starting point; confirm how its existing paths should
   support the recursive concept.
2. Run one short coding example across multiple decomposition levels and watch it.
3. Define per-node: inputs, permitted tool operations, and the execute-vs-decompose
   qualifier.
4. Define how dependencies / constraints / clarification / completion propagate.
5. Decide model assignments (local coder for decomposition, stronger model for hard
   leaves) and evaluation criteria.
6. Benchmark local performance against the live Odin endpoints above.
