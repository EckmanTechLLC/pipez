"""LLM layer: one structured decision call per task node.

Replaces V13's ClaudeClient + JSONValidator — the schema is enforced by the
API itself (output_config json_schema). Every call carries the server-side
web-search tool so nodes ground answers in current real-world specifics, and
the `facts` fields carry provenance for every specific an answer commits to.
Server-side refusal fallbacks are enabled by default.
"""
import asyncio
import copy
import logging
import os

from . import config
from .providers import (AnthropicProvider, LLMError, OpenAICompatProvider,
                        is_transient)

logger = logging.getLogger("pipez.llm")

FACTS_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "claim": {"type": "string"},
            "value": {"type": "string"},
            "source": {"type": "string"},
        },
        "required": ["claim", "value", "source"],
        "additionalProperties": False,
    },
}

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "output": {"type": "string"},
        "facts": FACTS_SCHEMA,
    },
    "required": ["summary", "output", "facts"],
    "additionalProperties": False,
}

_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["decompose", "complete"]},
        "reasoning": {"type": "string"},
        "shared_context": {
            "type": "string",
            "description": "Constraints, parameters, and data every subtask needs. Subtasks see nothing else.",
        },
        "subtasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "role": {"type": "string"},
                    "title": {"type": "string"},
                    "prompt": {"type": "string"},
                    "tier": {"type": "string", "enum": []},
                    "effort": {"type": "string",
                               "enum": ["low", "medium", "high", "xhigh", "max"]},
                    "depends_on": {"type": "array",
                                   "description": "Non-negative indices of earlier subtasks in this array",
                                   "items": {"type": "integer"}},
                },
                "required": ["role", "title", "prompt", "tier"],
                "additionalProperties": False,
            },
        },
        "result": RESULT_SCHEMA,
    },
    "required": ["action", "reasoning"],
    "additionalProperties": False,
}


def decision_schema() -> dict:
    """The decision schema with the subtask tier enum taken from the tiers
    configured right now (so `local` appears only when it exists)."""
    schema = copy.deepcopy(_DECISION_SCHEMA)
    schema["properties"]["subtasks"]["items"]["properties"]["tier"]["enum"] = \
        list(config.MODEL_TIERS)
    return schema


VERDICTS_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "verdict": {"type": "string",
                                "enum": ["confirmed", "unverifiable", "wrong"]},
                    "note": {"type": "string"},
                    "correction": {"type": "string"},
                },
                "required": ["claim", "verdict", "note", "correction"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "verdicts"],
    "additionalProperties": False,
}


_OPTION_SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "string"}, "label": {"type": "string"},
                   "consequences": {"type": "string"}},
    "required": ["value", "label", "consequences"],
    "additionalProperties": False,
}

# Structured outputs cannot express an open {key: value} map under
# additionalProperties:false, so the wire form of request_values is a list of
# {key, value} pairs; LLM.intake() converts it to the dict form.
INTAKE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "request_values": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"key": {"type": "string"},
                               "value": {"type": "string"}},
                "required": ["key", "value"],
                "additionalProperties": False,
            },
        },
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "question": {"type": "string"},
                    "why_it_matters": {"type": "string"},
                    "options": {"type": "array", "items": _OPTION_SCHEMA,
                                "description": "2-6 options"},
                    "proposed_default": {"type": "string"},
                    "required": {"type": "boolean"},
                },
                "required": ["key", "question", "why_it_matters", "options",
                             "proposed_default", "required"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "request_values", "decisions"],
    "additionalProperties": False,
}

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "brief": {"type": "string"},
        "constraints": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["brief", "constraints", "open_questions"],
    "additionalProperties": False,
}


_EFFORT_ENUM = ["low", "medium", "high", "xhigh", "max"]

_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "architecture": {"type": "string"},
        "stack": {"type": "array", "items": {"type": "string"}},
        "repo_layout": {"type": "string"},
        "modules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "path": {"type": "string"},
                    "purpose": {"type": "string"},
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                    "contract": {"type": "string"},
                },
                "required": ["name", "path", "purpose", "depends_on", "contract"],
                "additionalProperties": False,
            },
        },
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "module": {"type": "string"},
                    "prompt": {"type": "string"},
                    "tier": {"type": "string", "enum": []},
                    "effort": {"type": "string", "enum": _EFFORT_ENUM},
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                    "acceptance": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "title", "module", "prompt", "tier",
                             "depends_on", "acceptance"],
                "additionalProperties": False,
            },
        },
        "scaffold_commands": {"type": "array", "items": {"type": "string"}},
        "build_command": {"type": "string"},
        "test_command": {"type": "string"},
        "deploy": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "enum": ["vm", "none"]},
                "install_commands": {"type": "array", "items": {"type": "string"}},
                "start_command": {"type": "string"},
                "port": {"type": "integer"},
                "health_path": {"type": "string"},
                "env": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"name": {"type": "string"},
                                       "value": {"type": "string"}},
                        "required": ["name", "value"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["target", "install_commands", "start_command", "port",
                         "health_path", "env"],
            "additionalProperties": False,
        },
    },
    "required": ["architecture", "stack", "repo_layout", "modules", "tasks",
                 "scaffold_commands"],
    "additionalProperties": False,
}


def plan_schema() -> dict:
    """The plan schema with the task tier enum taken from the tiers
    configured right now."""
    schema = copy.deepcopy(_PLAN_SCHEMA)
    schema["properties"]["tasks"]["items"]["properties"]["tier"]["enum"] = \
        list(config.MODEL_TIERS)
    return schema


class LLM:
    """Facade over the providers: dispatches by tier["provider"] and wraps
    each call in slow task-level retries."""

    def __init__(self, providers: dict = None):
        self.providers = providers or {
            "anthropic": AnthropicProvider(),
            "openai_compat": OpenAICompatProvider(),
        }

    def _resolve_tier(self, tier: dict = None) -> dict:
        tier = tier or config.MODEL_TIERS[config.DEFAULT_TIER]
        if tier.get("provider", "anthropic") not in self.providers:
            raise LLMError("unsupported provider: %r" % tier.get("provider"))
        return tier

    async def _call(self, system: str, prompt: str, schema: dict,
                    fetch=False, tier: dict = None) -> tuple[dict, dict]:
        """One schema-constrained call with slow task-level retries around the
        SDK's quick ones: transient failures (network, 5xx, rate limit) and
        truncation are retried with tens-of-seconds backoff; refusals are not."""
        last_error = None
        for attempt in range(config.TASK_RETRIES + 1):
            if attempt:
                delay = config.RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning("retry %d/%d in %.0fs after: %s",
                               attempt, config.TASK_RETRIES, delay, last_error)
                await asyncio.sleep(delay)
            try:
                tier = self._resolve_tier(tier)
                provider = self.providers[tier.get("provider", "anthropic")]
                return await provider.complete(system, prompt, schema, tier,
                                               fetch=fetch)
            except Exception as e:
                if is_transient(e):
                    last_error = e
                elif isinstance(e, LLMError) and "truncated" in str(e):
                    last_error = e
                else:
                    raise
        raise LLMError("failed after %d retries: %s"
                       % (config.TASK_RETRIES, last_error))

    @staticmethod
    def _require_anthropic(tier: dict, default: str) -> dict:
        tier = tier or config.MODEL_TIERS[default]
        if tier.get("provider", "anthropic") != "anthropic":
            raise LLMError("this call requires an Anthropic tier, got %r"
                           % tier.get("provider"))
        return tier

    async def decide(self, system: str, prompt: str, depth: int = 0,
                     tier: dict = None) -> tuple[dict, dict]:
        return await self._call(system, prompt, decision_schema(), tier=tier)

    async def synthesize(self, system: str, prompt: str,
                         tier: dict = None) -> tuple[dict, dict]:
        return await self._call(system, prompt, RESULT_SCHEMA, tier=tier)

    async def intake(self, system: str, prompt: str,
                     tier: dict = None) -> tuple[dict, dict]:
        out, usage = await self._call(system, prompt, INTAKE_SCHEMA, tier=tier)
        out = dict(out, request_values={
            p["key"]: p["value"] for p in out.get("request_values") or []})
        return out, usage

    async def brief(self, system: str, prompt: str,
                    tier: dict = None) -> tuple[dict, dict]:
        return await self._call(system, prompt, BRIEF_SCHEMA, tier=tier)

    async def plan(self, system: str, prompt: str,
                   tier: dict = None) -> tuple[dict, dict]:
        return await self._call(system, prompt, plan_schema(), tier=tier)

    async def verify(self, system: str, prompt: str,
                     tier: dict = None) -> tuple[dict, dict]:
        # fetch=True so the verifier can open cited URLs and audit them.
        # Verification always runs deep: the checker must not be weaker
        # than what it checks.
        tier = self._require_anthropic(tier, "deep")
        return await self._call(system, prompt, VERDICTS_SCHEMA, fetch=True,
                                tier=tier)

    async def render_artifacts(self, system: str, prompt: str,
                               dest_dir: str, tier: dict = None) -> tuple[list, dict]:
        tier = self._require_anthropic(tier, "standard")
        return await self.providers["anthropic"].render_artifacts(
            system, prompt, dest_dir, tier)


class MockLLM:
    """Deterministic tree for dev without an API key: the root decomposes into
    three subtasks, depth-1 nodes into two, everything deeper completes.
    Sleeps make the live table visibly progress."""

    async def decide(self, system: str, prompt: str, depth: int = 0,
                     tier: dict = None) -> tuple[dict, dict]:
        await asyncio.sleep(1.5)
        if "COMPLETE this task directly" in system or depth >= 2:
            return self._complete()
        n = 3 if depth == 0 else 2
        tiers = ["deep", "standard", "light"]
        subtasks = [
            {"role": f"mock-worker-{i+1}", "title": f"Mock subtask {i+1}",
             "prompt": f"mock work item {i+1}", "tier": tiers[i % 3]}
            for i in range(n)
        ]
        if depth == 0:
            subtasks[0]["effort"] = "low"  # exercises the per-subtask effort path
            subtasks[2]["depends_on"] = [0]  # exercises one dependency edge
        return {
            "action": "decompose",
            "reasoning": "mock decomposition",
            "shared_context": "mock shared context from depth %d" % depth,
            "subtasks": subtasks,
        }, {"input_tokens": 0, "output_tokens": 0, "model": "mock"}

    async def synthesize(self, system: str, prompt: str,
                         tier: dict = None) -> tuple[dict, dict]:
        await asyncio.sleep(1.0)
        return {"summary": "mock synthesis of child results",
                "output": "mock synthesized output",
                "facts": [{"claim": "mock synthesized fact", "value": "42",
                           "source": "model-knowledge"}]}, \
               {"input_tokens": 0, "output_tokens": 0, "model": "mock"}

    def _complete(self):
        return {"action": "complete", "reasoning": "mock leaf",
                "result": {"summary": "mock leaf done", "output": "mock leaf output",
                           "facts": [{"claim": "mock fact", "value": "3.14",
                                      "source": "https://example.com/mock"}]}}, \
               {"input_tokens": 0, "output_tokens": 0, "model": "mock"}

    async def intake(self, system: str, prompt: str,
                     tier: dict = None) -> tuple[dict, dict]:
        await asyncio.sleep(0.5)

        def opts(*vals):
            return [{"value": v, "label": v, "consequences": "mock: choosing %s" % v}
                    for v in vals]
        return {
            "summary": "mock intake summary",
            "request_values": {},
            "decisions": [
                {"key": "storage.database", "question": "Which database?",
                 "why_it_matters": "Determines persistence and ops burden.",
                 "options": opts("postgres", "sqlite", "none"),
                 "proposed_default": "sqlite", "required": False},
                {"key": "license", "question": "Which license?",
                 "why_it_matters": "Legal terms of distribution.",
                 "options": opts("MIT", "Apache-2.0", "proprietary"),
                 "proposed_default": "MIT", "required": True},
                {"key": "hosting.target", "question": "Where will it run?",
                 "why_it_matters": "Shapes packaging and deployment.",
                 "options": opts("vm", "container", "static"),
                 "proposed_default": "container", "required": False},
            ],
        }, {"input_tokens": 0, "output_tokens": 0, "model": "mock"}

    async def brief(self, system: str, prompt: str,
                    tier: dict = None) -> tuple[dict, dict]:
        await asyncio.sleep(0.5)
        # One open question until the prompt carries the resolved answers.
        oq = [] if "Resolved open questions" in prompt else \
            ["Which name should the product go by?"]
        return {"brief": "# Build brief (mock)\n\nBuild what was requested, "
                         "honouring the decisions below.",
                "constraints": ["Honour every decision in the table."],
                "open_questions": oq}, \
               {"input_tokens": 0, "output_tokens": 0, "model": "mock"}

    async def plan(self, system: str, prompt: str,
                   tier: dict = None) -> tuple[dict, dict]:
        await asyncio.sleep(0.5)
        arch = "Two components: core (domain logic) and api (HTTP layer over core)."
        if "REVISION NOTE" in prompt:
            arch = "Revised: " + arch
        return {
            "architecture": arch,
            "stack": ["python", "fastapi", "sqlite"],
            "repo_layout": "app/\n  core/\n  api/\ntests/",
            "modules": [
                {"name": "core", "path": "app/core", "purpose": "Domain logic",
                 "depends_on": [],
                 "contract": "CONTRACT-CORE: exposes run(item) -> Result; owns "
                             "app/core/*.py; tested with pytest in tests/core."},
                {"name": "api", "path": "app/api", "purpose": "HTTP layer",
                 "depends_on": ["core"],
                 "contract": "CONTRACT-API: exposes POST /run calling core.run; "
                             "owns app/api/*.py; tested with TestClient."},
            ],
            "tasks": [
                {"id": "t1", "title": "Core data model", "module": "core",
                 "prompt": "Implement the core data model.", "tier": "standard",
                 "depends_on": [], "acceptance": ["models import cleanly"]},
                {"id": "t2", "title": "Core logic", "module": "core",
                 "prompt": "Implement core.run.", "tier": "light",
                 "effort": "low", "depends_on": [],
                 "acceptance": ["run() returns a Result", "unit tests pass"]},
                {"id": "t3", "title": "API endpoint", "module": "api",
                 "prompt": "Implement POST /run.", "tier": "standard",
                 "depends_on": ["t1"], "acceptance": ["endpoint returns 200"]},
            ],
            "scaffold_commands": ["git init", "mkdir -p tests"],
            "build_command": "true",
            "test_command": "python3 -m pytest -q",
            "deploy": {
                "target": "vm",
                "install_commands": ["sudo apt-get install -y python3"],
                "start_command": "python3 -m http.server {port}",
                "port": 8080,
                "health_path": "/",
                "env": [],
            },
        }, {"input_tokens": 0, "output_tokens": 0, "model": "mock"}

    async def verify(self, system: str, prompt: str,
                     tier: dict = None) -> tuple[dict, dict]:
        await asyncio.sleep(1.0)
        return {"summary": "mock verification",
                "verdicts": [
                    {"claim": "mock fact", "verdict": "confirmed",
                     "note": "matches mock source", "correction": ""},
                    {"claim": "mock synthesized fact", "verdict": "unverifiable",
                     "note": "no source available", "correction": ""},
                ]}, {"input_tokens": 0, "output_tokens": 0, "model": "mock"}

    async def render_artifacts(self, system: str, prompt: str,
                               dest_dir: str, tier: dict = None) -> tuple[list, dict]:
        await asyncio.sleep(1.0)
        os.makedirs(dest_dir, exist_ok=True)
        files = []
        for name, content in (("report.md", "# Mock report\n\nmock content\n"),
                              ("data.csv", "item,value\nmock,42\n")):
            path = os.path.join(dest_dir, name)
            with open(path, "w") as f:
                f.write(content)
            files.append({"filename": name, "path": path,
                          "bytes": os.path.getsize(path)})
        return files, {"input_tokens": 0, "output_tokens": 0, "model": "mock"}


def make_llm():
    return MockLLM() if config.MOCK else LLM()
