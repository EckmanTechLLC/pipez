"""PIPEZ configuration — environment-driven, no domain knowledge here.

A .env file in the project root is loaded first (KEY=value lines; existing
environment wins), so the API key can live with the project.
"""
import os

_env_file = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(_env_file):
    with open(_env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# LLM — model tiers. The decomposing parent assigns each subtask a tier;
# which model backs a tier is config, so swapping models never touches
# prompts. Root, verify: deep. Render: standard. Synthesis: the node's tier.
MODEL_TIERS = {
    "deep":     {"provider": "anthropic", "model": os.environ.get("PIPEZ_MODEL_DEEP", "claude-opus-5-5"),     "effort": os.environ.get("PIPEZ_EFFORT_DEEP", "high")},
    "standard": {"provider": "anthropic", "model": os.environ.get("PIPEZ_MODEL_STANDARD", "claude-sonnet-5-5"), "effort": os.environ.get("PIPEZ_EFFORT_STANDARD", "medium")},
    "light":    {"provider": "anthropic", "model": os.environ.get("PIPEZ_MODEL_LIGHT", "claude-haiku-5-5"),    "effort": os.environ.get("PIPEZ_EFFORT_LIGHT", "low")},
}

# Local tier: an OpenAI-compatible endpoint (e.g. vLLM serving qwen3). The
# tier exists only when PIPEZ_LOCAL_BASE_URL is set.
LOCAL_BASE_URL = os.environ.get("PIPEZ_LOCAL_BASE_URL", "")
LOCAL_MODEL = os.environ.get("PIPEZ_LOCAL_MODEL", "")
LOCAL_API_KEY = os.environ.get("PIPEZ_LOCAL_API_KEY", "")
LOCAL_TIMEOUT = float(os.environ.get("PIPEZ_LOCAL_TIMEOUT", "600"))
LOCAL_TEMPERATURE = float(os.environ.get("PIPEZ_LOCAL_TEMPERATURE", "0.2"))
LOCAL_THINKING = os.environ.get("PIPEZ_LOCAL_THINKING", "0") == "1"
if LOCAL_BASE_URL:
    MODEL_TIERS["local"] = {"provider": "openai_compat", "model": LOCAL_MODEL,
                            "effort": "low", "base_url": LOCAL_BASE_URL}
DEFAULT_TIER = "deep"

# Escalate a failed call to the next tier up (cheapest first).
ESCALATE_ON_FAILURE = os.environ.get("PIPEZ_ESCALATE", "1") == "1"
TIER_ORDER = ["local", "light", "standard", "deep"]


def next_tier_up(name: str):
    """The next configured tier above `name`, or None (never past deep)."""
    if name not in TIER_ORDER:
        return None
    for cand in TIER_ORDER[TIER_ORDER.index(name) + 1:]:
        if cand in MODEL_TIERS:
            return cand
    return None
MAX_TOKENS = int(os.environ.get("PIPEZ_MAX_TOKENS", "128000"))

# Task-level retries for transient failures (network outages, 5xx, rate
# limits) — these back off on a scale of tens of seconds, beyond the SDK's
# own quick retries, so a brief network blip doesn't kill a long run.
TASK_RETRIES = int(os.environ.get("PIPEZ_TASK_RETRIES", "3"))
RETRY_BASE_DELAY = float(os.environ.get("PIPEZ_RETRY_DELAY", "20"))
# Server-side refusal fallbacks (Claude API beta). On a policy decline the API
# re-runs the request on a fallback model inside the same call.
ENABLE_FALLBACKS = os.environ.get("PIPEZ_FALLBACKS", "1") == "1"

# Mock mode: no API calls, deterministic decomposition — for dev UI work.
MOCK = os.environ.get("PIPEZ_MOCK", "0") == "1"

# Server-side web search: grounds answers in current real-world specifics.
ENABLE_SEARCH = os.environ.get("PIPEZ_SEARCH", "1") == "1"
SEARCH_MAX_USES = int(os.environ.get("PIPEZ_SEARCH_MAX_USES", "5"))

# Tree limits (behavior caps, not a class hierarchy)
MAX_DEPTH = int(os.environ.get("PIPEZ_MAX_DEPTH", "4"))
MAX_CHILDREN = int(os.environ.get("PIPEZ_MAX_CHILDREN", "8"))
MAX_TASKS_PER_RUN = int(os.environ.get("PIPEZ_MAX_TASKS", "100"))

# Parallelism: concurrent LLM calls across the whole engine
MAX_CONCURRENCY = int(os.environ.get("PIPEZ_CONCURRENCY", "4"))

# Artifact rendering: turn a finished run's deliverable into real files
# (PDF, CSV/XLSX, diagrams) via the server-side code-execution sandbox.
ENABLE_ARTIFACTS = os.environ.get("PIPEZ_ARTIFACTS", "1") == "1"

# Preferences: a global default graduates when PREF_GRADUATE of the last
# PREF_WINDOW observations agree.
PREF_WINDOW = int(os.environ.get("PIPEZ_PREF_WINDOW", "5"))
PREF_GRADUATE = int(os.environ.get("PIPEZ_PREF_GRADUATE", "3"))

# Intake: how many brief rounds may ask the human open questions (the last
# round turns remaining open questions into explicit assumptions).
INTAKE_MAX_ROUNDS = int(os.environ.get("PIPEZ_INTAKE_MAX_ROUNDS", "2"))

# Plan parsimony: hard limits stated to the architect; exceeding them is
# logged and warned about, never rejected. PLAN_EFFORT overrides the deep
# tier's effort for the planning call only.
PLAN_MAX_TASKS = int(os.environ.get("PIPEZ_PLAN_MAX_TASKS", "12"))
PLAN_MAX_MODULES = int(os.environ.get("PIPEZ_PLAN_MAX_MODULES", "8"))
PLAN_EFFORT = os.environ.get("PIPEZ_PLAN_EFFORT", "high")

# Coding leaves (task 07c): one Agent SDK session per code task in the sandbox.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
LEAF_MAX_TURNS = int(os.environ.get("PIPEZ_LEAF_MAX_TURNS", "60"))
LEAF_MAX_BUDGET_USD = float(os.environ.get("PIPEZ_LEAF_MAX_BUDGET_USD", "5.0"))
LEAF_TIMEOUT = float(os.environ.get("PIPEZ_LEAF_TIMEOUT", "1800"))
INTEGRATE_TIMEOUT = float(os.environ.get("PIPEZ_INTEGRATE_TIMEOUT", "1800"))
LEAF_REQUIRE_TESTS = os.environ.get("PIPEZ_LEAF_REQUIRE_TESTS", "1") == "1"
# Extra attempts for a code task whose runner errored or whose tests failed.
LEAF_RETRIES = int(os.environ.get("PIPEZ_LEAF_RETRIES", "1"))
# Fixer rounds when build/test on `integration` fails.
FIX_ROUNDS = int(os.environ.get("PIPEZ_FIX_ROUNDS", "2"))

# Storage
DB_PATH = os.environ.get("PIPEZ_DB", os.path.join(os.path.dirname(__file__), "..", "pipez.sqlite3"))
ARTIFACTS_DIR = os.environ.get(
    "PIPEZ_ARTIFACTS_DIR", os.path.join(os.path.dirname(__file__), "..", "artifacts"))

# Server
HOST = os.environ.get("PIPEZ_HOST", "127.0.0.1")
PORT = int(os.environ.get("PIPEZ_PORT", "8400"))

# Proxmox sandboxes (task 07b). The token secret lives only here: the rest of
# the code uses PVE_AUTH_HEADER and never logs it.
PVE_URL = os.environ.get("PIPEZ_PVE_URL", "").rstrip("/")
PVE_TOKEN_ID = os.environ.get("PIPEZ_PVE_TOKEN_ID", "")
PVE_AUTH_HEADER = "PVEAPIToken=%s=%s" % (
    PVE_TOKEN_ID, os.environ.get("PIPEZ_PVE_TOKEN_SECRET", ""))
PVE_NODE = os.environ.get("PIPEZ_PVE_NODE", "")
PVE_POOL = os.environ.get("PIPEZ_PVE_POOL", "pipez")
PVE_TEMPLATE = int(os.environ.get("PIPEZ_PVE_TEMPLATE", "9001") or 9001)
PVE_VMID_RANGE = os.environ.get("PIPEZ_PVE_VMID_RANGE", "9100-9199")
PVE_VERIFY_TLS = os.environ.get("PIPEZ_PVE_VERIFY_TLS", "1") == "1"

# pve|mock; mock whenever PIPEZ_MOCK=1.
SANDBOX = os.environ.get("PIPEZ_SANDBOX", "mock" if MOCK else "pve")
SANDBOX_BOOT_TIMEOUT = float(os.environ.get("PIPEZ_SANDBOX_BOOT_TIMEOUT", "240"))
SSH_KEY = os.path.expanduser(os.environ.get("PIPEZ_SSH_KEY", "~/.ssh/pipez_ed25519"))
SANDBOX_KEEP_ON_FAILURE = os.environ.get("PIPEZ_SANDBOX_KEEP_ON_FAILURE", "1") == "1"

# Debug: keep mock sandbox temp dirs after teardown (inspect repo/worktrees).
SANDBOX_KEEP = os.environ.get("PIPEZ_SANDBOX_KEEP", "0") == "1"

# Deploy (task 09): a finished build is installed on a fresh VM cloned from the
# plain Ubuntu template (not the builder) and kept as the deliverable.
DEPLOY_AUTO = os.environ.get("PIPEZ_DEPLOY_AUTO", "1") == "1"
DEPLOY_TEMPLATE = int(os.environ.get("PIPEZ_PVE_DEPLOY_TEMPLATE", "9000") or 9000)
DEPLOY_VMID_RANGE = os.environ.get("PIPEZ_PVE_DEPLOY_VMID_RANGE", "9200-9299")
DEPLOY_HEALTH_TIMEOUT = float(os.environ.get("PIPEZ_DEPLOY_HEALTH_TIMEOUT", "180"))

# Acceptance review (task 13a): after a deploy is ready, a read-only reviewer
# leaf checks the build against the plan's acceptance criteria.
VERIFY_AUTO = os.environ.get("PIPEZ_VERIFY_AUTO", "1") == "1"
VERIFY_TIER = os.environ.get("PIPEZ_VERIFY_TIER", "standard")
# Fix rounds after a failed acceptance review (fixer -> re-integrate -> fresh deploy -> review).
VERIFY_ROUNDS = int(os.environ.get("PIPEZ_VERIFY_ROUNDS", "1"))
