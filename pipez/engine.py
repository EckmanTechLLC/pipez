"""The recursive engine.

One node type. Each task makes a single structured decision — decompose or
complete. Children run in parallel under a global concurrency cap; the parent
synthesizes over whatever succeeded. Every transition is persisted to SQLite
first, then published to the event bus for the live table.
"""
import ast
import asyncio
import json
import logging
import os
import re
import shlex
import time

from . import config, seed_prompts
from .llm import LLMError
from .prefs import PreferenceStore
from .providers import OpenAICompatProvider
from .deploy import Deployer, make_deploy_sandbox
from .pve import PveError, ProxmoxClient
from .sandbox import make_sandbox, _reserved as _reserved_vmids

logger = logging.getLogger("pipez.engine")

RUNNER_DIR = os.path.join(os.path.dirname(__file__), "..", "infra", "runner")

EFFORTS = ("low", "medium", "high", "xhigh", "max")


class SkippedError(LLMError):
    """A child never ran because a task it depends on failed or was skipped."""


class ScaffoldError(Exception):
    """A scaffold command failed; the run fails before any leaf starts."""


class IntegrationError(LLMError):
    """Build/test/git failure on `integration`. fixable: a fixer leaf may be
    able to repair it (build/test failed, dirty tree); cmd/rc/tail describe
    the failing command for the fixer's prompt."""

    def __init__(self, msg, fixable=False, cmd=None, rc=None, tail=None):
        super().__init__(msg)
        self.fixable, self.cmd, self.rc, self.tail = fixable, cmd, rc, tail


TERMINAL = ("done", "failed", "cancelled")
# Task statuses a stop turns into `cancelled`.
ACTIVE_TASK = ("running", "pending", "awaiting_children", "synthesizing",
               "awaiting_human")
REVIEW_ROLE = "acceptance-reviewer"    # task 13a: the read-only acceptance reviewer leaf
MAX_TASK_QUESTIONS = 3     # per code task, before it fails instead of asking again

BASELINE_GITIGNORE = ("__pycache__/\n*.pyc\n.pytest_cache/\n.mypy_cache/\n"
                      ".ruff_cache/\nnode_modules\n.venv\ndist/\nbuild/\n.pipez/\n")


def k_empty(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


BASH_REPR = re.compile(r"name='Bash', input=\{'command': ('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")")


class Engine:
    def __init__(self, db, bus, llm, prefs=None, sandbox_factory=None,
                 deploy_factory=None, pve_factory=None):
        self.db = db
        self.bus = bus
        self.llm = llm
        self.prefs = prefs or PreferenceStore(db)
        self.semaphore = asyncio.Semaphore(config.MAX_CONCURRENCY)
        self._runs: set[asyncio.Task] = set()
        self._run_tasks: dict[str, asyncio.Task] = {}   # run_id -> latest driver
        self._waiters: dict[str, asyncio.Event] = {}    # task_id -> human answer
        self._sandbox_factory = sandbox_factory or make_sandbox
        self._sandboxes: dict = {}
        self._deploy_factory = deploy_factory or make_deploy_sandbox
        self._deploys: dict = {}     # run_id -> deploy sandbox
        self._pve_factory = pve_factory     # None: ProxmoxClient() when PVE is configured
        self._merge_locks: dict = {}
        self._kills: set = set()     # in-flight remote runner kills
        self._local_down = False
        self._probe_task = None
        if "local" in config.MODEL_TIERS:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop:
                self._probe_task = loop.create_task(self._probe_local())

    async def _probe_local(self):
        """One-shot health probe of the local provider; on failure, tasks
        assigned to `local` resolve to the next tier up."""
        providers = getattr(self.llm, "providers", None) or {}
        provider = providers.get("openai_compat") or OpenAICompatProvider()
        try:
            ok = await provider.healthy()
        except Exception:
            ok = False
        if not ok:
            logger.warning("local provider at %s is unavailable; "
                           "local-tier tasks will use the next tier up",
                           config.LOCAL_BASE_URL)
            self._local_down = True

    async def _llm_call(self, task: dict, fn):
        """Run fn(tier_dict) for the task's tier, applying the per-task effort
        override, the local-down fallback, and escalation on LLMError."""
        task_id = task["id"]
        task = self.db.get_task(task_id) or task
        name = task.get("tier") or config.DEFAULT_TIER
        if name not in config.MODEL_TIERS:
            name = config.DEFAULT_TIER

        def move(new, reason):
            nonlocal name
            self.db.log(task_id, "escalated",
                        {"from": name, "to": new, "reason": reason})
            self._update(task_id, tier=new)
            name = new

        if name == "local" and self._local_down:
            new = config.next_tier_up("local")
            if new:
                move(new, "local provider unavailable")
        while True:
            tier = config.MODEL_TIERS[name]
            if task.get("effort"):
                tier = dict(tier, effort=task["effort"])
            try:
                async with self.semaphore:
                    return await fn(tier)
            except LLMError as e:
                new = config.next_tier_up(name) if config.ESCALATE_ON_FAILURE else None
                if not new:
                    raise
                move(new, str(e))

    def _add_spend(self, run_id, task_id, stage, usage, source="estimate", cost=None):
        """Record one priced call in the spend ledger (never raises)."""
        try:
            usage = usage or {}
            model = usage.get("model")
            i, o = int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
            if cost is None:
                cost = config.estimate_cost(model, i, o)
            self.db.add_spend(run_id, task_id, stage, model, i, o, cost, source)
            if self.db.get_run(run_id):
                self._publish_run(run_id)
        except Exception:
            logger.exception("recording spend for run %s failed", run_id)

    async def _run_llm_call(self, run_id: str, tier_name: str, fn):
        """Run-level counterpart of _llm_call (intake has no task row):
        fn(tier_dict) with escalation on LLMError; escalations are logged to
        task_log under task_id 'run:<run_id>'."""
        log_id = "run:%s" % run_id
        name = tier_name if tier_name in config.MODEL_TIERS else config.DEFAULT_TIER
        if name == "local" and self._local_down:
            new = config.next_tier_up("local")
            if new:
                self.db.log(log_id, "escalated",
                            {"from": name, "to": new,
                             "reason": "local provider unavailable"})
                name = new
        while True:
            try:
                async with self.semaphore:
                    return await fn(config.MODEL_TIERS[name]), name
            except LLMError as e:
                new = config.next_tier_up(name) if config.ESCALATE_ON_FAILURE else None
                if not new:
                    raise
                self.db.log(log_id, "escalated",
                            {"from": name, "to": new, "reason": str(e)})
                name = new

    # -- public -------------------------------------------------------------

    def _spawn(self, coro, run_id: str = None):
        t = asyncio.get_running_loop().create_task(coro)
        self._runs.add(t)
        t.add_done_callback(self._runs.discard)
        if run_id:
            self._run_tasks[run_id] = t

            def _forget(done, run_id=run_id):
                if self._run_tasks.get(run_id) is done:
                    del self._run_tasks[run_id]
            t.add_done_callback(_forget)
        return t

    def _alive(self, run_id: str) -> bool:
        t = self._run_tasks.get(run_id)
        return t is not None and not t.done()

    def submit(self, request: str, context: str = None,
               parent_run_id: str = None) -> str:
        """Create a run in status `intake` and start discovery in the
        background. The root task is created after the human answers."""
        run_id = self.db.create_run(request, context, parent_run_id,
                                    status="intake")
        self._publish_run(run_id)
        self._publish_intake(run_id)
        self._spawn(self._intake(run_id), run_id)
        return run_id

    # -- intake -------------------------------------------------------------

    def _publish_intake(self, run_id: str):
        run = self.db.get_run(run_id)
        self.bus.publish({"type": "intake", "run_id": run_id,
                          "status": run["status"]})

    def _set_phase(self, run_id: str, status: str):
        self.db.set_run_status(run_id, status)
        self._publish_run(run_id)
        self._publish_intake(run_id)

    def _known_keys_section(self, project_id) -> str:
        """Decision keys discovery should reuse: those with observations
        (with their resolved value and source) plus the starter vocabulary."""
        lines, seen = [], set()
        for k, r in self.prefs.resolve_all(project_id).items():
            if k.startswith("open."):
                continue
            seen.add(k)
            if r["value"] is None:
                lines.append("- %s" % k)
            else:
                n = (r.get("evidence") or {}).get("count")
                lines.append("- %s = %s (%s%s)" % (
                    k, r["value"], r["source"],
                    ", n=%s" % n if n is not None else ""))
        vocab = self.db.get_prompt("intake_key_vocabulary") \
            or seed_prompts.DEFAULTS["intake_key_vocabulary"]
        for k in vocab.split():
            if k not in seen:
                lines.append("- %s" % k)
        return ("# Known decision keys\nReuse a key below whenever your "
                "decision is the same concept; invent a new dotted key only "
                "for a genuinely new concept.\n" + "\n".join(lines))

    def _intake_prompt(self, run: dict) -> tuple:
        """(system, prompt) of the intake discovery call for a run row."""
        system = self.db.get_prompt("intake_discovery") or ""
        parts = ["# Request\n%s" % run["request"]]
        if run.get("context"):
            parts.append("# Project context\n%s" % run["context"])
        parts.append(self._known_keys_section(run["project_id"]))
        return system, "\n\n".join(parts)

    async def _intake(self, run_id: str):
        """Discovery: ask the intake tier for human-necessary decisions,
        pre-fill each from the preference store, pause as awaiting_human."""
        try:
            run = self.db.get_run(run_id)
            system, prompt = self._intake_prompt(run)
            started = time.time()
            (out, usage), tier_name = await self._run_llm_call(
                run_id,
                "local" if config.local_allowed("intake") else config.INTAKE_TIER,
                lambda tier: self.llm.intake(system, prompt, tier))
            self._add_spend(run_id, None, "intake", usage)
            request_values = out.get("request_values") or {}
            decisions = []
            for d in out.get("decisions") or []:
                opts = d.get("options") or []
                if len(opts) < 2 and d["key"] not in request_values:
                    self.db.log("run:%s" % run_id, "intake_invalid",
                                {"key": d.get("key"), "options": len(opts)})
                elif len(opts) > 6:
                    d = dict(d, options=opts[:6])
                r = self.prefs.resolve(d["key"], run["project_id"], request_values)
                if r["source"] == "none":
                    r = dict(r, value=d["proposed_default"], source="model")
                decisions.append(dict(
                    d, prefill={"value": r["value"], "source": r["source"],
                                "evidence": r["evidence"]},
                    locked=d["key"] in request_values))
            standing = []
            for sp in out.get("standing_preferences") or []:
                k, v = (sp.get("key") or "").strip(), (sp.get("value") or "").strip()
                if not k or not v or k.startswith("open."):
                    continue
                cur = self.prefs.resolve(k, run["project_id"])
                if cur["source"] == "pinned" and cur["value"] == v:
                    continue
                standing.append({"key": k, "value": v,
                                 "evidence": sp.get("evidence") or ""})
            if standing:
                self.db.log("run:%s" % run_id, "standing_prefs",
                            {"keys": [s["key"] for s in standing]})
            meta = {"summary": out.get("summary", ""), "tier": tier_name,
                    "standing_preferences": standing,
                    "model": usage.get("model"),
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "seconds": round(time.time() - started, 3),
                    "request_values": request_values}
            self.db.set_intake(run_id, decisions, meta)
            self._set_phase(run_id, "awaiting_human")
        except Exception as e:
            logger.exception("intake of run %s failed", run_id)
            self.db.finish_run(run_id, "failed", error=str(e))
            self._publish_run(run_id)
            self._publish_intake(run_id)

    def answer_intake(self, run_id: str, answers: dict = None,
                      reasons: dict = None, accept_all: bool = False) -> dict:
        """Validate the human's answers, record each as an observation, and
        start the brief + build. Raises ValueError (-> 400) on bad input."""
        run = self.db.get_run(run_id)
        if not run:
            raise ValueError("no such run")
        if run["status"] != "awaiting_human":
            raise ValueError("run is %s, not awaiting_human" % run["status"])
        if any(t["status"] == "awaiting_human" for t in self.db.get_tasks(run_id)):
            raise ValueError("run is waiting on a task question — answer it "
                             "via the task answer endpoint")
        decisions = run.get("decisions") or []
        by_key = {d["key"]: d for d in decisions}
        prior = dict(run.get("answers") or {})
        # Later rounds only accept answers to decisions not yet answered.
        answers = {k: v for k, v in (answers or {}).items()
                   if k not in prior or k not in by_key}
        reasons = reasons or {}
        unknown = sorted(set(answers) - set(by_key))
        if unknown:
            raise ValueError("unknown decision keys: %s" % ", ".join(unknown))
        for d in decisions:
            k = d["key"]
            if k not in answers and k not in prior \
                    and (accept_all or d["locked"]) \
                    and d["prefill"]["value"] is not None:
                answers[k] = d["prefill"]["value"]
        missing = [d["key"] for d in decisions
                   if d["required"] and k_empty(answers.get(d["key"],
                                                            prior.get(d["key"])))]
        if missing:
            raise ValueError("required decisions unanswered: %s"
                             % ", ".join(missing))
        sources = {}
        for k, v in answers.items():
            d = by_key[k]
            pre = d["prefill"]
            if d.get("free_text"):
                self.prefs.observe(k, v, "request", "intake",
                                   run["project_id"], run_id, reasons.get(k))
                sources[k] = "intake"
                continue
            if d["locked"]:
                src = "request"
            elif pre["source"] != "model" and v != pre["value"]:
                src = "override"
            else:
                src = "intake"
            sources[k] = src
            self.prefs.observe(k, v, "global", src, run["project_id"], run_id,
                               reasons.get(k))
        sources.update({k: ("request" if by_key[k]["locked"] else "intake")
                        for k in prior if k in by_key})
        answers = dict(prior, **answers)
        self.db.set_answers(run_id, answers)
        self._set_phase(run_id, "briefing")
        self._spawn(self._brief_and_start(run_id, sources), run_id)
        return answers

    @staticmethod
    def _cell(text) -> str:
        return str(text if text is not None else "").replace(
            "|", "\\|").replace("\n", " ")

    def _decision_table(self, decisions, answers, sources) -> str:
        rows = ["| key | question | chosen | consequences | source |",
                "|---|---|---|---|---|"]
        for d in decisions:
            k = d["key"]
            if k not in answers or d.get("free_text"):
                continue
            opt = next((o for o in d["options"] if o["value"] == answers[k]), None)
            rows.append("| %s | %s | %s | %s | %s |" % tuple(self._cell(x) for x in (
                k, d["question"], answers[k],
                opt["consequences"] if opt else "(custom answer)",
                sources.get(k, "intake"))))
        return "\n".join(rows)

    def _brief_prompt(self, run: dict, sources: dict = None) -> tuple:
        """(system, prompt) of the brief call for a run row; sources maps
        decision key -> where its answer came from (default "intake")."""
        decisions, answers = run["decisions"], run["answers"] or {}
        table = self._decision_table(decisions, answers, sources or {})
        opens = [d for d in decisions if d.get("free_text")]
        resolved = "\n".join(
            "- Q: %s (key: %s)\n  A: %s" % (
                d["question"], d["key"], answers[d["key"]])
            for d in opens if d["key"] in answers)
        system = self.db.get_prompt("intake_brief") or ""
        parts = ["# Original request\n%s" % run["request"]]
        if run.get("context"):
            parts.append("# Project context\n%s" % run["context"])
        parts.append("# Answered decisions\n%s" % table)
        if resolved:
            parts.append("# Resolved open questions\n%s" % resolved)
        return system, "\n\n".join(parts)

    def _record_normalised(self, run: dict, items: list):
        """Record brief-call normalised_answers as global intake observations
        (never pins). Accepts only free_text answers mapped onto known keys
        that this run has not answered itself."""
        run_id, answers = run["id"], run["answers"] or {}
        free = {d["key"] for d in run["decisions"] if d.get("free_text")}
        known = {k for k in self.prefs.resolve_all(run["project_id"])
                 if not k.startswith("open.")}
        known.update((self.db.get_prompt("intake_key_vocabulary")
                      or seed_prompts.DEFAULTS["intake_key_vocabulary"]).split())
        accepted, rejected = [], []
        for it in items:
            k, v, fk = it.get("key"), it.get("value"), it.get("from_key")
            if fk in free and fk in answers and k and v \
                    and not k.startswith("open.") and k in known \
                    and k not in answers:
                self.prefs.observe(k, v, "global", "intake", run["project_id"],
                                   run_id, reason="normalised from %s" % fk)
                accepted.append({"key": k, "value": v, "from_key": fk})
            else:
                rejected.append({"key": k, "value": v, "from_key": fk})
        self.db.log("run:%s" % run_id, "normalised_answers",
                    {"accepted": accepted, "rejected": rejected})

    async def _brief_and_start(self, run_id: str, sources: dict = None):
        try:
            run = self.db.get_run(run_id)
            decisions, answers = run["decisions"], run["answers"] or {}
            sources = sources or {}
            table = self._decision_table(decisions, answers, sources)
            opens = [d for d in decisions if d.get("free_text")]
            resolved = "\n".join(
                "- Q: %s\n  A: %s" % (d["question"], answers[d["key"]])
                for d in opens if d["key"] in answers)
            rnd = max([d.get("round", 1) for d in opens] or [0]) + 1
            system, prompt = self._brief_prompt(run, sources)
            started = time.time()
            (out, usage), tier_name = await self._run_llm_call(
                run_id, "local" if config.local_allowed("brief") else "standard",
                lambda tier: self.llm.brief(system, prompt, tier))
            self._add_spend(run_id, None, "brief", usage)
            text = out["brief"].rstrip()
            if out.get("constraints"):
                text += "\n\n## Constraints\n" + "\n".join(
                    "- %s" % c for c in out["constraints"])
            questions = [q for q in out.get("open_questions") or [] if q]
            if questions and rnd < config.INTAKE_MAX_ROUNDS:
                new, taken = [], {d["key"] for d in decisions}
                for q in questions:
                    slug = "_".join(re.findall(r"[a-z0-9]+", q.lower())[:6]) \
                        or "question"
                    key, n = "open." + slug, 2
                    while key in taken:
                        key, n = "open.%s_%d" % (slug, n), n + 1
                    taken.add(key)
                    new.append({
                        "key": key, "question": q,
                        "why_it_matters": "The brief could not settle this",
                        "options": [], "proposed_default": "",
                        "required": True, "free_text": True, "locked": False,
                        "round": rnd,
                        "prefill": {"value": None, "source": "none",
                                    "evidence": {}}})
                self.db.set_decisions(run_id, decisions + new)
                self.db.log("run:%s" % run_id, "open_questions",
                            {"round": rnd, "keys": [d["key"] for d in new]})
                self._set_phase(run_id, "awaiting_human")
                return
            self._record_normalised(run, out.get("normalised_answers") or [])
            if resolved:
                text += "\n\n## Resolved open questions\n" + resolved
            if questions:
                text += "\n\n## Assumptions\n" + "\n".join(
                    "- Unresolved, assumed (flag in output): %s" % q
                    for q in questions)
            brief = "%s\n\n## Decisions\n\n%s\n" % (text, table)
            self.db.set_brief(run_id, brief, {
                "tier": tier_name, "model": usage.get("model"),
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "seconds": round(time.time() - started, 3),
                "constraints": out.get("constraints", []),
                "open_questions": questions, "round": rnd})
            self._set_phase(run_id, "planning")
        except Exception as e:
            logger.exception("brief of run %s failed", run_id)
            self.db.finish_run(run_id, "failed", error=str(e))
            self._publish_run(run_id)
            self._publish_intake(run_id)
            return
        await self._plan(run_id)

    # -- plan ---------------------------------------------------------------

    def _publish_plan(self, run_id: str):
        run = self.db.get_run(run_id)
        self.bus.publish({"type": "plan", "run_id": run_id,
                          "status": run["status"]})

    def _set_plan_phase(self, run_id: str, status: str):
        self.db.set_run_status(run_id, status)
        self._publish_run(run_id)
        self._publish_plan(run_id)

    def _seed_bundle(self, run: dict):
        """Path of the parent's repo bundle when `run` is a child of a plan
        run that produced one, else None."""
        parent = run.get("parent_run_id")
        if not parent:
            return None
        path = os.path.join(config.ARTIFACTS_DIR, parent, "repo-%s.bundle" % parent)
        return path if os.path.isfile(path) else None

    def _existing_repo_section(self, run: dict) -> str:
        """The `# Existing repository` plan-prompt section for a child run
        whose workspace is seeded from its parent's bundle ('' otherwise)."""
        if not self._seed_bundle(run):
            return ""
        parent = run["parent_run_id"]
        pplan = (self.db.get_run(parent) or {}).get("plan") or {}
        lines = [
            "# Existing repository",
            "The repository from the prior run %s is ALREADY present in the "
            "workspace at branch `main` (tag `pipez/%s`). Plan an increment on "
            "top of it." % (parent, parent),
            "Parent stack: %s" % (", ".join(pplan.get("stack") or []) or "(unknown)"),
            "Parent repo layout:\n%s" % (pplan.get("repo_layout") or "(unknown)"),
            "Do NOT re-scaffold: no `git init`, `npm init` or `git checkout` of "
            "tags. scaffold_commands may only install dependencies (e.g. `npm ci`) "
            "and create new directories.",
        ]
        if pplan.get("build_command"):
            lines.append("Parent build_command: %s" % pplan["build_command"])
        if pplan.get("test_command"):
            lines.append("Parent test_command: %s" % pplan["test_command"])
        return "\n".join(lines)

    @staticmethod
    def _is_revision(run: dict) -> bool:
        """True when `run` carries a previous plan and a human revision note."""
        revisions = (run.get("plan_meta") or {}).get("revisions") or []
        return bool(run.get("plan") and revisions)

    def _plan_prompt(self, run: dict) -> tuple:
        """(system, prompt) of the plan call; includes the previous plan and
        the last human note when `run` is a revision."""
        meta = run.get("plan_meta") or {}
        revisions = meta.get("revisions") or []
        system = (self.db.get_prompt("plan_architect") or "") + (
            "\n\nHARD LIMITS: at most %d tasks and at most %d modules. "
            "Merge small tasks; a task is a unit one engineer completes "
            "in one sitting, not a function."
            % (config.PLAN_MAX_TASKS, config.PLAN_MAX_MODULES))
        parts = ["# Original request\n%s" % run["request"]]
        if run.get("context"):
            parts.append("# Project context\n%s" % run["context"])
        parts.append("# Build brief (binding)\n%s" % run.get("brief"))
        existing = self._existing_repo_section(run)
        if existing:
            parts.append(existing)
        system += "\n\nAllowed tiers: %s." % ", ".join(config.offered_tiers("plan"))
        if run.get("plan") and revisions:
            parts.append("# Previous plan\n%s"
                         % json.dumps(run["plan"], indent=2))
            parts.append("REVISION NOTE (from the human reviewer; revise "
                         "the previous plan accordingly):\n%s"
                         % revisions[-1]["note"])
        return system, "\n\n".join(parts)

    async def _plan(self, run_id: str):
        """Planning: the deep tier turns the brief into architecture, module
        contracts and a task DAG; the run then pauses as awaiting_plan."""
        try:
            run = self.db.get_run(run_id)
            meta = run.get("plan_meta") or {}
            revisions = meta.get("revisions") or []
            system, prompt = self._plan_prompt(run)
            revision = self._is_revision(run)
            plan_tier = config.PLAN_REVISION_TIER if revision else "deep"
            plan_effort = config.PLAN_REVISION_EFFORT if revision else config.PLAN_EFFORT
            self.db.log("run:%s" % run_id, "plan_call",
                        {"revision": revision, "tier": plan_tier,
                         "effort": plan_effort})
            started = time.time()
            (out, usage), tier_name = await self._run_llm_call(
                run_id, plan_tier, lambda tier: self.llm.plan(
                    system, prompt, dict(tier, effort=plan_effort)))
            self._add_spend(run_id, None, "plan", usage)
            plan = self._validate_plan(run_id, out)
            warnings = []
            nt, nm = len(plan.get("tasks") or []), len(plan.get("modules") or [])
            if nt > config.PLAN_MAX_TASKS:
                warnings.append("plan has %d tasks (limit %d)"
                                % (nt, config.PLAN_MAX_TASKS))
            if nm > config.PLAN_MAX_MODULES:
                warnings.append("plan has %d modules (limit %d)"
                                % (nm, config.PLAN_MAX_MODULES))
            if warnings:
                self.db.log("run:%s" % run_id, "plan_oversize",
                            {"tasks": nt, "modules": nm,
                             "max_tasks": config.PLAN_MAX_TASKS,
                             "max_modules": config.PLAN_MAX_MODULES})
            self.db.set_plan(run_id, plan, {
                "warnings": warnings,
                "tier": tier_name, "model": usage.get("model"),
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "seconds": round(time.time() - started, 3),
                "revisions": revisions})
            self._set_plan_phase(run_id, "awaiting_plan")
        except Exception as e:
            logger.exception("planning of run %s failed", run_id)
            self.db.finish_run(run_id, "failed", error=str(e))
            self._publish_run(run_id)
            self._publish_plan(run_id)

    @staticmethod
    def _break_cycles(deps: dict) -> list:
        """Drop the edges that close a cycle in {node: [dependency, ...]}
        (mutated in place); returns [(node, dep)] dropped. DFS along
        "depends on" edges; an edge into a node on the current path closes a
        cycle."""
        dropped = []
        state = {k: 0 for k in deps}  # 0 new, 1 on path, 2 finished
        for root in deps:
            if state[root]:
                continue
            state[root] = 1
            stack = [(root, iter(list(deps[root])))]
            while stack:
                node, it = stack[-1]
                for j in it:
                    if state[j] == 1:
                        deps[node].remove(j)
                        dropped.append((node, j))
                    elif state[j] == 0:
                        state[j] = 1
                        stack.append((j, iter(list(deps[j]))))
                        break
                else:
                    state[node] = 2
                    stack.pop()
        return dropped

    @staticmethod
    def _clean_deploy(d) -> dict:
        """Normalise plan.deploy; anything unusable becomes target `none`."""
        d = d if isinstance(d, dict) else {}
        try:
            port = int(d.get("port") or 0)
        except (TypeError, ValueError):
            port = 0
        out = {
            "target": d.get("target") if d.get("target") in ("vm", "none") else "none",
            "install_commands": [str(c).strip() for c in d.get("install_commands") or []
                                 if str(c).strip()],
            "start_command": str(d.get("start_command") or "").strip(),
            "port": port,
            "health_path": str(d.get("health_path") or "/").strip() or "/",
            "env": [],
        }
        if not out["health_path"].startswith("/"):
            out["health_path"] = "/" + out["health_path"]
        for e in d.get("env") or []:
            if isinstance(e, dict) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*",
                                                    str(e.get("name") or "")):
                out["env"].append({"name": e["name"], "value": str(e.get("value") or "")})
        if out["target"] == "vm" and (not out["start_command"]
                                      or not 0 < port < 65536):
            out["target"] = "none"      # cannot deploy without a command and a port
        return out

    def _validate_plan(self, run_id: str, plan: dict) -> dict:
        """Return a cleaned copy of the plan. Unknown / duplicate / cyclic
        references are dropped and logged as `plan_invalid` on task_log
        `run:<id>`; a bad edge never fails the plan. No tasks -> LLMError."""
        if not isinstance(plan, dict):
            raise LLMError("plan is not an object")
        invalid = []
        plan = dict(plan)
        plan.setdefault("architecture", "")
        plan["stack"] = [str(x) for x in plan.get("stack") or []]
        plan.setdefault("repo_layout", "")
        plan["scaffold_commands"] = [str(x) for x in plan.get("scaffold_commands") or []]
        for k in ("build_command", "test_command"):
            plan[k] = str(plan.get(k) or "").strip()
        plan["deploy"] = self._clean_deploy(plan.get("deploy"))

        def clean(items, key, what):
            seen, out = set(), []
            for it in items or []:
                if not isinstance(it, dict) or not it.get(key):
                    invalid.append({"what": what, "reason": "missing %s" % key})
                elif it[key] in seen:
                    invalid.append({"what": what, "id": it[key],
                                    "reason": "duplicate"})
                else:
                    seen.add(it[key])
                    out.append(dict(it))
            return out

        def fix_edges(items, key, what):
            names = {it[key] for it in items}
            for it in items:
                kept = []
                for d in it.get("depends_on") or []:
                    if d == it[key]:
                        invalid.append({"what": what, "id": it[key], "dep": d,
                                        "reason": "self-reference"})
                    elif d not in names:
                        invalid.append({"what": what, "id": it[key], "dep": d,
                                        "reason": "unknown"})
                    elif d not in kept:
                        kept.append(d)
                it["depends_on"] = kept
            graph = {it[key]: it["depends_on"] for it in items}
            for node, dep in self._break_cycles(graph):
                invalid.append({"what": what, "id": node, "dep": dep,
                                "reason": "cycle"})

        modules = clean(plan.get("modules"), "name", "module")
        fix_edges(modules, "name", "module")
        tasks = clean(plan.get("tasks"), "id", "task")
        fix_edges(tasks, "id", "task")
        for t in tasks:
            t["acceptance"] = [str(a) for a in t.get("acceptance") or []]
            if t.get("tier") not in config.MODEL_TIERS:
                invalid.append({"what": "task", "id": t["id"],
                                "reason": "unknown tier %r -> deep" % t.get("tier")})
                t["tier"] = "deep"
            if t.get("effort") not in EFFORTS:
                t.pop("effort", None)
        if invalid:
            self.db.log("run:%s" % run_id, "plan_invalid", {"dropped": invalid})
        if not tasks:
            raise LLMError("plan has no tasks")
        plan["modules"], plan["tasks"] = modules, tasks
        return plan

    def answer_plan(self, run_id: str, approve: bool, note: str = None,
                    plan: dict = None) -> dict:
        """The plan gate. approve -> (optionally replace the plan with an
        edited, re-validated one and) materialise the task tree and run it;
        otherwise a non-empty note is required and the plan is redone.
        Raises ValueError (-> 400)."""
        run = self.db.get_run(run_id)
        if not run:
            raise ValueError("no such run")
        if run["status"] != "awaiting_plan":
            raise ValueError("run is %s, not awaiting_plan" % run["status"])
        meta = run.get("plan_meta") or {}
        if approve:
            if plan is not None:
                try:
                    plan = self._validate_plan(run_id, plan)
                except LLMError as e:
                    raise ValueError(str(e))
                self.db.set_plan(run_id, plan, meta)
            root = self._materialise_plan(run_id)
            self._set_plan_phase(run_id, "provisioning")
            self._spawn(self._run_with_sandbox(run_id, root), run_id)
            return self.db.get_run(run_id)
        note = (note or "").strip()
        if not note:
            raise ValueError("a note is required to request changes")
        revisions = list(meta.get("revisions") or []) + [
            {"note": note, "ts": time.time()}]
        self.db.set_plan(run_id, run.get("plan"), dict(meta, revisions=revisions),
                         revisions=(run.get("plan_revisions") or 0) + 1)
        self._set_plan_phase(run_id, "planning")
        self._spawn(self._plan(run_id), run_id)
        return self.db.get_run(run_id)

    def _materialise_plan(self, run_id: str) -> dict:
        """Create the task tree from the approved plan: one integrator root
        whose children are the plan's code tasks, wired with depends_on."""
        run = self.db.get_run(run_id)
        plan = run["plan"]
        modules = {m["name"]: m for m in plan.get("modules") or []}
        context = "%s\n\n## Architecture\n%s\n\n## Repo layout\n%s\n\n## Module contracts\n%s" % (
            run["brief"], plan.get("architecture", ""), plan.get("repo_layout", ""),
            "\n\n".join("### %s (%s)\n%s" % (m["name"], m.get("path", ""),
                                               m.get("contract", ""))
                         for m in modules.values()))
        root = self.db.create_task(
            run_id=run_id, parent_id=None, depth=0, seq=0, role="integrator",
            title=run["request"][:120],
            prompt="Integrate the completed modules into the deliverable "
                   "described by the brief.\n\n%s" % run["brief"],
            context=context, kind="llm")
        self._publish_task(root)
        children = []
        for i, t in enumerate(plan["tasks"]):
            if t.get("tier") == "local":    # code tasks run on the Agent SDK
                self.db.log("run:%s" % run_id, "tier_coerced",
                            {"task": t.get("id"), "from": "local", "to": "light",
                             "reason": "code tasks run on the Agent SDK"})
                t["tier"] = "light"
            mod = modules.get(t.get("module"))
            prompt = t["prompt"]
            if mod:
                prompt += "\n\n## Module contract (%s)\n%s" % (
                    mod["name"], mod.get("contract", ""))
            children.append(self.db.create_task(
                run_id=run_id, parent_id=root["id"], depth=1, seq=i,
                role="%s-engineer" % t.get("module", "code"), title=t["title"],
                prompt=prompt, tier=t["tier"], effort=t.get("effort"),
                kind="code", module=t.get("module"),
                acceptance=t.get("acceptance") or []))
        ids = {t["id"]: c["id"] for t, c in zip(plan["tasks"], children)}
        for t, c in zip(plan["tasks"], children):
            if t.get("depends_on"):
                self.db.update_task(c["id"], depends_on=[
                    ids[d] for d in t["depends_on"] if d in ids])
        for c in self.db.get_children(root["id"]):
            self._publish_task(c)
        return root

    # -- run lifecycle ------------------------------------------------------

    async def _run(self, run_id: str, root: dict):
        try:
            result = await self._execute(root)
            if config.ENABLE_ARTIFACTS:
                await self._render_artifacts(run_id, root, result)
            self.db.finish_run(run_id, "done", result=result)
        except Exception as e:
            logger.exception("run %s failed", run_id)
            self.db.finish_run(run_id, "failed", error=str(e))
        self._publish_run(run_id)

    def resume(self, run_id: str) -> int:
        """Resume an interrupted run from the checkpoint that is the task
        tree: done tasks return their stored results instantly; failed,
        stale, and pending tasks re-execute; parents whose children exist
        skip the decision call and go straight to (re-)synthesis."""
        run = self.db.get_run(run_id)
        if not run:
            raise ValueError("no such run")
        tasks = self.db.get_tasks(run_id)
        if run["status"] == "awaiting_human" and not (tasks and not self._alive(run_id)):
            raise ValueError("run is awaiting_human — nothing to resume; "
                             "answer the intake or the task question instead")
        if run["status"] == "awaiting_plan":
            raise ValueError("run is awaiting_plan — nothing to resume; "
                             "approve or revise the plan instead")
        if run["status"] == "cancelled" and not tasks and run.get("plan"):
            # stopped at the plan gate: back to it
            self.db.set_run_status(run_id, "awaiting_plan", clear_error=True)
            self._publish_run(run_id)
            self._publish_plan(run_id)
            return 0
        if (run["status"] == "planning"
                or (not tasks and run["status"] in ("failed", "cancelled")
                    and run.get("brief"))):
            self.db.set_run_status(run_id, "planning", clear_error=True)
            self._publish_run(run_id)
            self._publish_plan(run_id)
            self._spawn(self._plan(run_id), run_id)
            return 0
        if run["status"] in ("intake", "briefing") or not tasks:
            # No tree yet (or a failure before it existed): redo intake.
            self.db.set_run_status(run_id, "intake", clear_error=True)
            self._publish_run(run_id)
            self._publish_intake(run_id)
            self._spawn(self._intake(run_id), run_id)
            return 0
        tasks = [t for t in tasks if t["role"] != REVIEW_ROLE]
        has_failures = any(t["status"] != "done" for t in tasks)
        if (run["status"] not in ("failed", "running", "cancelled")
                and not has_failures):
            raise ValueError("run is %s with no failed tasks — nothing to resume"
                             % run["status"])
        root = next(t for t in tasks if not t["parent_id"])
        if run.get("plan"):
            self.db.set_run_status(run_id, "provisioning", clear_error=True)
            self._publish_run(run_id)
            self._spawn(self._run_with_sandbox(run_id, root), run_id)
            return sum(1 for x in tasks if x["status"] != "done")
        self.db.set_run_status(run_id, "running", clear_error=True)
        self._publish_run(run_id)
        self._spawn(self._run_resume(run_id, root), run_id)
        return sum(1 for x in tasks if x["status"] != "done")

    # -- run control: stop, task questions (task 08) --------------------------

    def _mark_cancelled(self, run_id: str):
        """Make the DB consistent after a stop: every active task row becomes
        `cancelled`, the run becomes `cancelled`. Idempotent."""
        for t in self.db.get_tasks(run_id):
            if t["status"] in ACTIVE_TASK:
                self._update(t["id"], status="cancelled",
                             error="stopped by user", finished_at=time.time())
                self.db.log(t["id"], "cancelled", {"was": t["status"]})
        if self.db.get_run(run_id)["status"] not in TERMINAL:
            self.db.finish_run(run_id, "cancelled", error="stopped by user")
        self._publish_run(run_id)
        self._publish_intake(run_id)
        self._publish_plan(run_id)

    async def stop(self, run_id: str) -> dict:
        """Cancel a run at any phase. The run's driver task is cancelled, active
        task rows and the run become `cancelled`, a sandbox is kept for
        inspection. resume() later continues from the cancelled tasks."""
        run = self.db.get_run(run_id)
        if not run:
            raise ValueError("no such run")
        if run["status"] in TERMINAL:
            raise ValueError("run is %s, nothing to stop" % run["status"])
        if run["status"] == "verifying" and not run.get("plan"):
            raise ValueError("run is verifying, not stoppable")
        t = self._run_tasks.get(run_id)
        if t is not None and not t.done():
            t.cancel()
            await asyncio.gather(t, return_exceptions=True)
            await asyncio.gather(*list(self._kills), return_exceptions=True)
        elif run["status"] == "deploying":     # orphaned (server restarted)
            await self._abort_deploy(run_id, "stopped by user")
        self._mark_cancelled(run_id)
        self.db.log("run:" + run_id, "stopped", {})
        return self.db.get_run(run_id)

    # -- restart recovery and orphan sweep (task 15) ---------------------------

    INTERRUPTIBLE = ("intake", "briefing", "planning", "provisioning", "running",
                     "deploying", "verifying")
    INTERRUPTED = "interrupted by server restart"

    async def recover(self) -> dict:
        """Called once at startup, before serving. Nothing is auto-resumed:
        runs that were mid-flight become `failed` ("interrupted by server
        restart") and the human resumes them; their live sandboxes are kept so
        resume reuses them, half-built deploy VMs are destroyed."""
        out = {"runs": [], "sandboxes": [], "deployments": []}
        for run in self.db.get_runs(limit=100000):
            rid = run["id"]
            if run["status"] not in self.INTERRUPTIBLE or self._alive(rid):
                continue
            for t in self.db.get_tasks(rid):
                if t["status"] in ACTIVE_TASK:
                    self._update(t["id"], status="cancelled", error=self.INTERRUPTED,
                                 finished_at=time.time())
                    self.db.log(t["id"], "cancelled", {"was": t["status"]})
            self.db.finish_run(rid, "failed", error=self.INTERRUPTED)
            self.db.log("run:" + rid, "interrupted", {"was": run["status"]})
            self._publish_run(rid)
            out["runs"].append(rid)
        for row in self.db.list_sandboxes():
            rid = row["run_id"]
            if row["status"] not in ("provisioning", "ready") or self._alive(rid):
                continue
            try:
                sb = self._sandbox_factory(rid)
                if row["kind"] != sb.kind:
                    logger.warning("recover: sandbox of run %s is %s, not %s; left alone",
                                   rid, row["kind"], sb.kind)
                    continue
                sb.adopt(row)
                alive = bool(row.get("vmid") or row["kind"] == "mock") and await sb.alive()
            except Exception as e:  # noqa: BLE001
                logger.warning("recover: sandbox of run %s not checked: %s", rid, e)
                alive = False
            if alive:
                try:
                    await sb.kill_runner()      # the agent may still be running there
                except Exception as e:  # noqa: BLE001
                    logger.warning("recover: runner kill on run %s failed: %s", rid, e)
                self._sandbox_row(rid, status="kept", kept=1)
                self.db.log("run:" + rid, "sandbox_kept",
                            {"vmid": row["vmid"], "ip": row["ip"], "recovered": True})
            else:
                self._sandbox_row(rid, status="destroyed", destroyed_at=time.time(),
                                  kept=0, error=self.INTERRUPTED)
            out["sandboxes"].append({"run_id": rid, "kept": alive})
        # rows already `kept` before this restart: re-attach their handles
        for row in self.db.list_sandboxes():
            rid = row["run_id"]
            if row["status"] != "kept" or rid in self._sandboxes or self._alive(rid):
                continue
            out["sandboxes"].append({"run_id": rid,
                                     "attached": await self._attach_kept(rid) is not None})
        for row in self.db.list_deployments():
            rid = row["run_id"]
            if row["status"] not in ("provisioning", "installing", "starting") \
                    or self._alive(rid):
                continue
            try:
                await self._destroy_deploy_vm(rid, row)
            except Exception as e:  # noqa: BLE001
                logger.warning("recover: deploy VM of run %s not destroyed: %s", rid, e)
            self.db.upsert_deployment(rid, status="failed", error=self.INTERRUPTED)
            self.db.log("run:" + rid, "deploy_failed", {"error": self.INTERRUPTED})
            self._publish_deployment(rid)
            out["deployments"].append(rid)
        return out

    @staticmethod
    def _vmid_range(spec: str) -> range:
        lo, _, hi = spec.partition("-")
        return range(int(lo), int(hi or lo) + 1)

    async def sweep(self) -> list:
        """Reconcile the Proxmox pool with the DB. Pool VMs in the sandbox or
        deploy VMID range that no live row tracks are reported as
        {vmid, name, action} (destroyed when PIPEZ_SWEEP_DESTROY=1); rows that
        claim a VM which is gone become `destroyed`. No-op without PVE."""
        if self._pve_factory is None and (config.MOCK or config.SANDBOX == "mock"
                                          or not config.PVE_URL):
            return []
        ranges = [self._vmid_range(config.PVE_VMID_RANGE),
                  self._vmid_range(config.DEPLOY_VMID_RANGE)]
        in_range = lambda v: any(v in r for r in ranges)  # noqa: E731
        client = self._pve_factory() if self._pve_factory else ProxmoxClient()
        owned = self._pve_factory is None
        report: list = []
        try:
            try:
                members = [int(v) for v in await client.pool_members(config.PVE_POOL)]
            except PveError as e:
                logger.warning("sweep: pool listing failed: %s", e)
                return []
            member_set = set(members)
            tracked: set = set()
            sandboxes = self.db.list_sandboxes()
            deps = self.db.list_deployments()
            for r in sandboxes:
                if r["status"] in ("provisioning", "ready", "kept", "failed", "destroying"):
                    tracked.add(r["vmid"])
            for d in deps:
                if d["status"] in self.ACTIVE_DEPLOY + ("failed",):
                    tracked.add(d["vmid"])
            live = {r["run_id"] for r in sandboxes + deps if self._alive(r["run_id"])
                    or r["run_id"] in self._sandboxes or r["run_id"] in self._deploys}
            for vmid in sorted(member_set):
                if not in_range(vmid) or vmid in tracked or vmid in _reserved_vmids:
                    continue
                entry = {"vmid": vmid, "name": None, "action": "reported"}
                if config.SWEEP_DESTROY:
                    assert in_range(vmid), "sweep must never touch vmid %s" % vmid
                    try:
                        try:
                            await client.wait_task(await client.stop(vmid))
                        except PveError:
                            pass        # already stopped
                        await client.wait_task(await client.destroy(vmid))
                        entry["action"] = "destroyed"
                    except PveError as e:
                        entry["action"] = "destroy_failed: %s" % e
                logger.warning("sweep: orphan VM %s -> %s", vmid, entry["action"])
                report.append(entry)
            for r in sandboxes:
                if (r["status"] in ("kept", "ready", "provisioning") and r["vmid"]
                        and r["vmid"] not in member_set and r["run_id"] not in live):
                    self._sandbox_row(r["run_id"], status="destroyed",
                                      destroyed_at=time.time(), kept=0,
                                      error="VM no longer exists")
                    report.append({"vmid": r["vmid"], "name": r["name"],
                                   "action": "row_destroyed"})
            for d in deps:
                if (d["status"] in self.ACTIVE_DEPLOY and d["vmid"]
                        and d["vmid"] not in member_set and d["run_id"] not in live):
                    self.db.upsert_deployment(d["run_id"], status="destroyed", url=None,
                                              destroyed_at=time.time(),
                                              error="VM no longer exists")
                    self._publish_deployment(d["run_id"])
                    report.append({"vmid": d["vmid"], "name": d["name"],
                                   "action": "row_destroyed"})
            if report:
                self.db.log("sweep", "sweep", {"report": report})
        finally:
            if owned:
                await client.aclose()
        return report

    def _set_run_waiting(self, run_id: str):
        self.db.set_run_status(run_id, "awaiting_human")
        self._publish_run(run_id)
        self._publish_intake(run_id)

    @staticmethod
    def _question_key(nh: dict) -> str:
        key = str(nh.get("key") or "").strip()
        if not key:
            key = "_".join(re.findall(r"[a-z0-9]+",
                                      str(nh.get("question") or "").lower())[:6]) or "question"
        return key if "." in key else "task." + key

    async def _ask_human(self, task: dict, nh: dict):
        """A code task needs a human choice: park it as awaiting_human (the
        run too) and block this coroutine until answer_task() wakes it. The
        stored question carries the earlier answered ones under `previous`."""
        task_id, run_id = task["id"], task["run_id"]
        old = self.db.get_task(task_id).get("question") or {}
        previous = list(old.get("previous") or [])
        if old.get("answer"):
            previous.append({"key": old.get("key"), "question": old.get("question"),
                             "value": old["answer"].get("value"),
                             "reason": old["answer"].get("reason")})
        opts = [{"value": str(o.get("value", "")), "label": str(o.get("label") or o.get("value", "")),
                 "consequences": str(o.get("consequences") or "")}
                for o in (nh.get("options") or []) if isinstance(o, dict)]
        q = {"key": self._question_key(nh), "question": str(nh.get("question") or ""),
             "why_it_matters": str(nh.get("why_it_matters") or ""),
             "options": opts, "proposed_default": str(nh.get("proposed_default") or ""),
             "answer": None, "previous": previous}
        ev = self._waiters[task_id] = asyncio.Event()
        self._update(task_id, status="awaiting_human", question=q)
        self.db.log(task_id, "question", {"key": q["key"], "question": q["question"]})
        self._set_run_waiting(run_id)
        try:
            await ev.wait()
        finally:
            self._waiters.pop(task_id, None)

    def answer_task(self, run_id: str, task_id: str, value: str,
                    reason: str = None) -> dict:
        """Answer a task's question: record an observation (scope request,
        source intake) and the run answer, then let the task run again with
        the answer in its task.md. Raises ValueError (-> 400)."""
        run = self.db.get_run(run_id)
        task = self.db.get_task(task_id)
        if not run or not task or task["run_id"] != run_id:
            raise ValueError("no such task")
        q = task.get("question")
        if task["status"] != "awaiting_human" or not q:
            raise ValueError("task is %s, not awaiting_human" % task["status"])
        value = (value or "").strip()
        if not value:
            raise ValueError("an answer is required")
        reason = (reason or "").strip() or None
        self.prefs.observe(q["key"], value, "request", "intake",
                           run["project_id"], run_id, reason)
        self.db.set_answers(run_id, dict(run.get("answers") or {}, **{q["key"]: value}))
        self.db.log(task_id, "answered", {"key": q["key"], "value": value})
        live = task_id in self._waiters
        self._update(task_id, question=dict(q, answer={"value": value, "reason": reason}),
                     status="running" if live else "pending", error=None)
        if self.db.get_run(run_id)["status"] == "awaiting_human" and not any(
                t["status"] == "awaiting_human" for t in self.db.get_tasks(run_id)):
            self.db.set_run_status(run_id, "running")
        self._publish_run(run_id)
        self._publish_intake(run_id)
        if live:
            self._waiters[task_id].set()
        elif not self._alive(run_id):
            self.resume(run_id)     # server restarted while waiting: re-run it
        return self.db.get_task(task_id)

    # -- deploy to a lab VM (task 09) -----------------------------------------

    ACTIVE_DEPLOY = ("provisioning", "installing", "starting", "ready")

    def _tarball(self, run_id: str):
        name = "repo-%s.tar.gz" % run_id
        for a in self.db.get_artifacts(run_id):
            if a["filename"] == name and os.path.exists(a["path"]):
                return a["path"]
        return None

    def _deploy_spec(self, run_id: str):
        run = self.db.get_run(run_id)
        d = ((run or {}).get("plan") or {}).get("deploy") or {}
        return d if d.get("target") == "vm" else None

    def _wants_deploy(self, run_id: str) -> bool:
        dep = self.db.get_deployment(run_id)
        return bool(config.DEPLOY_AUTO and self._deploy_spec(run_id)
                    and self._tarball(run_id)
                    and not (dep and dep["status"] in self.ACTIVE_DEPLOY))

    def _publish_deployment(self, run_id: str):
        self.bus.publish({"type": "deployment", "run_id": run_id,
                          "deployment": self.db.get_deployment(run_id)})

    def deploy(self, run_id: str) -> dict:
        """Manual deploy of a finished run's tarball."""
        run = self.db.get_run(run_id)
        if not run:
            raise ValueError("no such run")
        if run["status"] != "done":
            raise ValueError("run is %s, only a done run can be deployed" % run["status"])
        if not self._tarball(run_id):
            raise ValueError("run has no tarball artifact to deploy")
        if not self._deploy_spec(run_id):
            raise ValueError("the plan has no VM deploy (deploy.target is not vm)")
        dep = self.db.get_deployment(run_id)
        if dep and dep["status"] in self.ACTIVE_DEPLOY:
            raise ValueError("deployment is %s, undeploy it first" % dep["status"])
        self.db.set_run_status(run_id, "deploying")
        self._publish_run(run_id)
        self._spawn(self._deploy(run_id), run_id)
        return self.db.get_run(run_id)

    async def _destroy_deploy_vm(self, run_id: str, row: dict):
        sb = self._deploys.pop(run_id, None)
        if sb is None:
            sb = self._deploy_factory(run_id)
            sb.adopt(row)
        await sb.teardown(False)

    async def _abort_deploy(self, run_id: str, reason: str):
        dep = self.db.get_deployment(run_id)
        if dep and dep["status"] in self.ACTIVE_DEPLOY[:-1]:
            try:
                await self._destroy_deploy_vm(run_id, dep)
            except Exception as e:
                logger.warning("deploy teardown for run %s failed: %s", run_id, e)
            self.db.upsert_deployment(run_id, status="failed", error=reason)
            self.db.log("run:" + run_id, "deploy_failed", {"error": reason})
            self._publish_deployment(run_id)
        if self.db.get_run(run_id)["status"] == "deploying":
            self.db.set_run_status(run_id, "done")
        self._publish_run(run_id)

    async def _deploy(self, run_id: str, review: bool = False):
        """Clone a fresh VM, install the run's tarball, start it as a service,
        wait for the health check. Failures never fail the run: the deployment
        becomes `failed` and the VM is kept for inspection. review: the caller
        runs the acceptance review next, so a ready deployment hands the run
        over as `verifying` instead of flickering through `done`."""
        key = "run:" + run_id
        lines: list = []
        sb = None
        try:
            run = self.db.get_run(run_id)
            plan = run["plan"]
            old = self.db.get_deployment(run_id)
            if old and old["status"] == "failed" and (old["vmid"] or old["name"]):
                try:        # a failed deployment's kept VM makes way for the new one
                    await self._destroy_deploy_vm(run_id, old)
                except Exception as e:
                    logger.warning("old deploy VM of run %s not destroyed: %s", run_id, e)
            self.db.replace_deployment(run_id)
            self.db.upsert_deployment(run_id, status="provisioning", log="")
            self.db.set_run_status(run_id, "deploying")
            self._publish_run(run_id)
            self._publish_deployment(run_id)
            sb = self._deploy_factory(run_id)
            self._deploys[run_id] = sb

            def report(status, **fields):
                self.db.upsert_deployment(run_id, status=status, **fields)
                self._publish_deployment(run_id)

            def note(line):
                lines.append(line)
                self.db.upsert_deployment(run_id, log="\n".join(lines)[-20000:])

            dep = Deployer(sb, run_id, plan, self._tarball(run_id), report,
                           lambda ev, d: self.db.log(key, ev, d), note)
            url = await dep.run()
            self.db.upsert_deployment(run_id, status="ready", url=url, ip=sb.ip,
                                      ready_at=time.time(), error=None,
                                      expires_at=self._expiry(False))
            self.db.log(key, "deploy_ok", {"url": url, "vmid": sb.vmid})
            await self.replace_deployments(run_id)
        except asyncio.CancelledError:
            await self._abort_deploy(run_id, "stopped by user")
            raise
        except Exception as e:
            logger.exception("deploy of run %s failed", run_id)
            msg = str(e)
            try:
                self.db.upsert_deployment(run_id, status="failed", error=msg,
                                          log="\n".join(lines + ["FAILED: " + msg])[-20000:])
                self.db.log(key, "deploy_failed", {"error": msg, "tail": msg[-1500:]})
                if sb is not None:
                    sb.kept = True      # the VM stays for inspection
            except Exception:
                logger.exception("recording deploy failure of run %s failed", run_id)
        finally:
            if self.db.get_run(run_id)["status"] == "deploying":
                dep = self.db.get_deployment(run_id)
                nxt = ("verifying" if review and config.VERIFY_AUTO and dep
                       and dep["status"] == "ready" else "done")
                self.db.set_run_status(run_id, nxt)
            self._publish_run(run_id)
            self._publish_deployment(run_id)

    @staticmethod
    def _expiry(keep: bool):
        if keep or not config.DEPLOY_TTL_HOURS:
            return None
        return time.time() + config.DEPLOY_TTL_HOURS * 3600

    def set_deploy_keep(self, run_id: str, keep: bool) -> dict:
        if not self.db.get_deployment(run_id):
            raise ValueError("no such deployment")
        row = self.db.upsert_deployment(run_id, keep=1 if keep else 0,
                                        expires_at=self._expiry(keep))
        self.db.log("run:" + run_id, "deploy_keep", {"keep": bool(keep)})
        self._publish_deployment(run_id)
        return row

    async def expire_deployments(self) -> list:
        """Undeploy ready, non-kept deployments past their expiry."""
        report = []
        now = time.time()
        for row in self.db.list_deployments():
            if (row["status"] != "ready" or row.get("keep")
                    or row.get("expires_at") is None or row["expires_at"] >= now):
                continue
            rid = row["run_id"]
            try:
                await self.undeploy(rid)
                self.db.log("run:" + rid, "deploy_expired",
                            {"expires_at": row["expires_at"], "vmid": row["vmid"]})
                report.append({"run_id": rid, "expired": True})
            except Exception as e:
                logger.warning("expiring deployment of run %s failed: %s", rid, e)
                self.db.log("run:" + rid, "deploy_expire_failed", {"error": str(e)})
                report.append({"run_id": rid, "expired": False, "error": str(e)})
        return report

    async def replace_deployments(self, run_id: str) -> list:
        """A ready deployment replaces its project's other ready, non-kept ones."""
        if not config.DEPLOY_REPLACE:
            return []
        dep = self.db.get_deployment(run_id)
        run = self.db.get_run(run_id)
        if not dep or dep["status"] != "ready" or not run:
            return []
        replaced = []
        for row in self.db.list_deployments():
            other = row["run_id"]
            if other == run_id or row["status"] != "ready" or row.get("keep"):
                continue
            orun = self.db.get_run(other)
            if not orun or orun.get("project_id") != run.get("project_id"):
                continue
            try:
                await self.undeploy(other)
                replaced.append(other)
            except Exception as e:
                logger.warning("replacing deployment of run %s failed: %s", other, e)
        if replaced:
            self.db.log("run:" + run_id, "deploy_replaced", {"replaced": replaced})
        return replaced

    async def undeploy(self, run_id: str) -> dict:
        row = self.db.get_deployment(run_id)
        if not row:
            raise ValueError("no such deployment")
        if row["status"] not in ("ready", "failed"):
            raise ValueError("deployment is %s, only ready or failed ones can be "
                             "undeployed" % row["status"])
        try:
            await self._destroy_deploy_vm(run_id, row)
        except Exception as e:
            self.db.upsert_deployment(run_id, error="undeploy failed: %s" % e)
            self._publish_deployment(run_id)
            raise ValueError("undeploy failed: %s" % e)
        self.db.upsert_deployment(run_id, status="destroyed", url=None,
                                  destroyed_at=time.time(), error=None)
        self.db.log("run:" + run_id, "undeployed", {"vmid": row["vmid"]})
        self._publish_deployment(run_id)
        return self.db.get_deployment(run_id)

    # -- sandbox lifecycle --------------------------------------------------

    def _sandbox_row(self, run_id: str, **fields):
        row = self.db.upsert_sandbox(run_id, **fields)
        self.bus.publish({"type": "sandbox", "run_id": run_id, "sandbox": row})
        return row

    async def _attach_kept(self, run_id: str):
        """Handle of a `kept` build sandbox, re-adopted from its DB row after a
        server restart. None when there is no such row, the kind differs, the
        VM is gone (row -> destroyed) or the check failed."""
        sb = self._sandboxes.get(run_id)
        if sb is not None:
            return sb
        key = "run:" + run_id
        try:
            row = self.db.get_sandbox(run_id)
            if not row or row["status"] != "kept":
                return None
            sb = self._sandbox_factory(run_id)
            if row["kind"] != sb.kind:
                logger.warning("attach: sandbox of run %s is %s, not %s",
                               run_id, row["kind"], sb.kind)
                return None
            sb.adopt(row)
            alive = bool(row.get("vmid") or row["kind"] == "mock") and await sb.alive()
            if alive:
                self._sandboxes[run_id] = sb
                self.db.log(key, "sandbox_attached", {"vmid": row["vmid"], "ip": row["ip"]})
                return sb
            self._sandbox_row(run_id, status="destroyed", destroyed_at=time.time(),
                              kept=0, error="kept sandbox is gone")
            self.db.log(key, "sandbox_gone", {"vmid": row["vmid"], "ip": row["ip"]})
        except Exception as e:  # noqa: BLE001
            logger.warning("attach: kept sandbox of run %s failed: %s", run_id, e)
            try:
                self.db.log(key, "sandbox_attach_failed", {"error": str(e)})
            except Exception:  # noqa: BLE001
                pass
        return None

    async def _acquire_sandbox(self, run_id: str):
        """Reuse a kept sandbox if it is still alive, else provision one."""
        old = self.db.get_sandbox(run_id)
        if old and old["status"] == "kept":
            # resume path: re-attach the kept sandbox if it is still there
            sb = await self._attach_kept(run_id)
            if sb is not None:
                self._sandbox_row(run_id, status="ready", kept=0, error=None)
                self.db.log("run:" + run_id, "sandbox_reused",
                            {"vmid": old["vmid"], "ip": old["ip"]})
                return sb
            self.db.log("run:" + run_id, "sandbox_replaced",
                        {"vmid": old["vmid"], "ip": old["ip"]})
        sb = self._sandbox_factory(run_id)
        self._sandbox_row(run_id, kind=sb.kind, vmid=None, name=sb.name, ip=None,
                          status="provisioning", created_at=time.time(),
                          ready_at=None, destroyed_at=None, error=None, kept=0)
        await sb.provision()
        self._sandbox_row(run_id, vmid=sb.vmid, name=sb.name, ip=sb.ip,
                          status="ready", ready_at=time.time())
        return sb

    async def _release_sandbox(self, run_id: str, keep: bool):
        sb = self._sandboxes.get(run_id)
        if sb is None:
            return
        try:
            if keep:
                await sb.teardown(True)
                self._sandbox_row(run_id, status="kept", kept=1)
                self.db.log("run:" + run_id, "sandbox_kept",
                            {"vmid": sb.vmid, "ip": sb.ip})
                logger.warning("run %s sandbox kept: vmid=%s ip=%s", run_id, sb.vmid, sb.ip)
            else:
                self._sandbox_row(run_id, status="destroying")
                await sb.teardown(False)
                self._sandbox_row(run_id, status="destroyed", destroyed_at=time.time(),
                                  kept=0)
                self._sandboxes.pop(run_id, None)
        except Exception as e:
            logger.exception("sandbox teardown for run %s failed", run_id)
            self._sandbox_row(run_id, status="failed", error=str(e))

    async def destroy_sandbox(self, run_id: str) -> dict:
        """Manual gc of a kept (or failed) sandbox."""
        row = self.db.get_sandbox(run_id)
        if not row:
            raise ValueError("no such sandbox")
        if row["status"] not in ("kept", "failed"):
            raise ValueError("sandbox is %s, only kept or failed ones can be destroyed"
                             % row["status"])
        if run_id not in self._sandboxes:
            if row["status"] == "kept":
                await self._attach_kept(run_id)
                if run_id not in self._sandboxes:       # gone: row already destroyed
                    return self.db.get_sandbox(run_id)
            else:
                sb = self._sandbox_factory(run_id)
                sb.adopt(row)
                self._sandboxes[run_id] = sb
        await self._release_sandbox(run_id, keep=False)
        return self.db.get_sandbox(run_id)

    async def _run_with_sandbox(self, run_id: str, root: dict):
        """_run_planned, with stop handling: on cancellation the task rows and
        the run become `cancelled` and the sandbox is kept for inspection."""
        try:
            await self._run_planned(run_id, root)
        except asyncio.CancelledError:
            self._mark_cancelled(run_id)
            row = self.db.get_sandbox(run_id)
            if row and row["status"] == "provisioning":
                self._sandbox_row(run_id, status="failed",
                                  error="stopped while provisioning")
            await self._release_sandbox(run_id, keep=True)
            raise

    async def _run_planned(self, run_id: str, root: dict):
        """provisioning -> running -> done|failed, then tear the sandbox down
        (destroy on success, keep on failure)."""
        try:
            self._sandboxes[run_id] = await self._acquire_sandbox(run_id)
        except Exception as e:
            logger.exception("sandbox provisioning for run %s failed", run_id)
            self._sandbox_row(run_id, status="failed", error=str(e))
            self.db.finish_run(run_id, "failed", error="sandbox provisioning failed: %s" % e)
            self._publish_run(run_id)
            return
        self.db.set_run_status(run_id, "running", clear_error=True)
        self.db.set_run_verification(run_id, None)
        self._publish_run(run_id)
        try:
            await self._init_workspace(run_id)
        except Exception as e:
            logger.exception("workspace init for run %s failed", run_id)
            msg = str(e) if isinstance(e, ScaffoldError) else "workspace_init_failed: %s" % e
            self.db.finish_run(run_id, "failed", error=msg)
            self._publish_run(run_id)
            await self._release_sandbox(run_id, keep=config.SANDBOX_KEEP_ON_FAILURE)
            return
        await self._run_resume(run_id, root)
        # Order: deploy -> acceptance review (needs the live sandbox) -> release.
        deploy_failed = None
        if self.db.get_run(run_id)["status"] == "deploying":
            await self._deploy(run_id, review=True)
            dep = self.db.get_deployment(run_id)
            if self._deploy_spec(run_id) and (not dep or dep["status"] != "ready"):
                deploy_failed = (dep or {}).get("error") or (
                    "deployment is %s" % (dep["status"] if dep else "absent"))
        if (self.db.get_run(run_id)["status"] in ("done", "verifying")
                and self._build_done(run_id)):
            if config.VERIFY_AUTO and deploy_failed is not None:
                self._review_error(run_id, "deployment failed: %s" % deploy_failed)
            elif config.VERIFY_AUTO:
                await self._review_with_fixes(run_id, root)
            else:
                self._review_skipped(run_id, "PIPEZ_VERIFY_AUTO is off")
        await self._finish_review(run_id)

    def _build_tasks(self, run_id: str) -> list:
        return [t for t in self.db.get_tasks(run_id) if t["role"] != REVIEW_ROLE]

    def _build_done(self, run_id: str) -> bool:
        return all(t["status"] == "done" for t in self._build_tasks(run_id))

    async def _finish_review(self, run_id: str):
        """The run ends `done` (flagged by `verification`); then release the
        sandbox. A run that finished with failed/skipped tasks, or whose review
        failed or errored, keeps its sandbox (resume / inspection)."""
        run = self.db.get_run(run_id)
        if run["status"] == "verifying":
            self.db.set_run_status(run_id, "done")
            self._publish_run(run_id)
        run = self.db.get_run(run_id)
        ok = (run["status"] == "done" and self._build_done(run_id)
              and run.get("verification") not in ("failed", "error"))
        await self._release_sandbox(
            run_id, keep=(not ok) and config.SANDBOX_KEEP_ON_FAILURE)

    # -- acceptance review (task 13a) ------------------------------------------

    def _review_skipped(self, run_id: str, why: str) -> str:
        self.db.log("run:" + run_id, "review_skipped", {"reason": why})
        self.db.set_run_verification(run_id, "skipped")
        self._publish_run(run_id)
        return "skipped"

    def _review_error(self, run_id: str, reason: str) -> str:
        """The review could not run for a reason that needs a human (e.g. the
        deployment failed): flag the run `error` so the sandbox is kept."""
        self.db.log("run:" + run_id, "review_error", {"reason": reason})
        self.db.set_run_verification(run_id, "error")
        self._publish_run(run_id)
        return "error"

    def _publish_verifications(self, run_id: str):
        self.bus.publish({"type": "verification", "run_id": run_id,
                          "verifications": self.db.get_verifications(run_id)})

    async def _review_with_fixes(self, run_id: str, root: dict = None) -> str:
        """_review; while it returns `failed` and rounds remain
        (PIPEZ_VERIFY_ROUNDS): fixer leaf on `integration` with the wrong
        verdicts as its brief, re-integrate, fresh deploy VM, review again.
        Any problem in the fix path stops the loop; verification stays `failed`."""
        key = "run:" + run_id
        if root is None:
            root = next(t for t in self.db.get_tasks(run_id) if not t["parent_id"])
        self.db.clear_verifications(run_id)
        rnd = 1
        while True:
            verification = await self._review(run_id, root, rnd)
            self.db.log(key, "review_round", {"round": rnd, "verification": verification})
            if verification != "failed" or rnd > config.VERIFY_ROUNDS:
                return verification
            vs = self.db.get_verifications(run_id)
            wrong = [v for v in json.loads(vs[-1]["verdicts"]) if v["verdict"] == "wrong"]
            kids = self.db.get_children(root["id"])
            n = sum(1 for c in kids if c.get("title", "").startswith("Fix review findings")) + 1
            run = self.db.get_run(run_id)
            lines = ["%d. Claim: %s\n   Problem: %s\n   Correction: %s" % (
                i, w["claim"], w["note"] or "-", w["correction"] or "-")
                for i, w in enumerate(wrong, 1)]
            prompt = ((("Relevant decisions (build brief):\n%s\n\n" % run["brief"])
                       if run.get("brief") else "")
                      + "An independent acceptance review of the deployed application "
                        "found these claims to be WRONG:\n\n%s\n\n"
                        "Make the full build and tests pass; change as little as possible; "
                        "keep public contracts." % "\n".join(lines))
            fix = self.db.create_task(
                run_id=run_id, parent_id=root["id"], depth=1,
                seq=max([c["seq"] for c in kids] or [-1]) + 1,
                role="integrator-engineer", title="Fix review findings (round %d)" % n,
                prompt=prompt, tier="deep", kind="code", module="*",
                acceptance=["The build and test commands pass on integration"])
            self._publish_task(fix)
            self.db.log(key, "review_fix", {"round": rnd, "task_id": fix["id"],
                                            "wrong": len(wrong)})
            try:
                await self._execute(fix)
                try:
                    await self._integrate(run_id, root)
                except IntegrationError as e:
                    if not e.fixable:
                        raise
                    await self._integrate_with_fixes(run_id, root)
                old = self.db.get_deployment(run_id)
                if old and old["status"] in ("ready", "failed"):
                    await self.undeploy(run_id)
                self.db.log(key, "review_redeploy", {
                    "round": rnd, "vmid": old["vmid"] if old else None})
                await self._deploy(run_id, review=True)
                dep = self.db.get_deployment(run_id)
                if not dep or dep["status"] != "ready":
                    self.db.log(key, "review_fix_stopped", {
                        "round": rnd, "reason": "redeploy is %s" % (
                            dep["status"] if dep else "absent")})
                    self.db.set_run_status(run_id, "verifying")
                    self._publish_run(run_id)
                    return "failed"
            except Exception as e:      # CancelledError is not an Exception
                logger.exception("review fix round %d of run %s failed", rnd, run_id)
                self.db.log(key, "review_fix_stopped", {"round": rnd, "reason": str(e)})
                return "failed"
            rnd += 1

    async def _review(self, run_id: str, root: dict = None, rnd: int = 1) -> str:
        """Independent read-only reviewer leaf over the live deployment. Stores
        its verdicts as a `verifications` row and sets runs.verification:
        passed (no `wrong`), failed (any `wrong`), error (runner failed / no
        verdicts), skipped (no plan / deployment not ready / no sandbox)."""
        key = "run:" + run_id
        run = self.db.get_run(run_id)
        plan = run.get("plan")
        dep = self.db.get_deployment(run_id)
        if not plan:
            return self._review_skipped(run_id, "run has no plan")
        if not dep or dep["status"] != "ready":
            return self._review_skipped(
                run_id, "deployment is %s" % (dep["status"] if dep else "absent"))
        if self._sandboxes.get(run_id) is None:
            return self._review_skipped(run_id, "no build sandbox")
        if root is None:
            root = next(t for t in self.db.get_tasks(run_id) if not t["parent_id"])
        self.db.set_run_verification(run_id, None)
        self.db.set_run_status(run_id, "verifying")
        self._publish_run(run_id)
        kids = self.db.get_children(root["id"])
        task = self.db.create_task(
            run_id=run_id, parent_id=root["id"], depth=1,
            seq=max([c["seq"] for c in kids] or [-1]) + 1, role=REVIEW_ROLE,
            title="Acceptance review", prompt=self.db.get_prompt("acceptance_reviewer") or "",
            tier=config.VERIFY_TIER, kind="code", module="*", acceptance=[])
        self._publish_task(task)
        self.db.log(key, "review_start", {"task_id": task["id"], "url": dep["url"]})
        verification, summary, verdicts = "error", "", []
        try:
            result = await self._execute(task)
            data = result.get("verdicts") or []
            summary = result.get("summary") or ""
            verdicts = [{"claim": str(v.get("claim") or ""),
                         "verdict": (v.get("verdict") if v.get("verdict") in (
                             "confirmed", "unverifiable", "wrong") else "unverifiable"),
                         "note": str(v.get("note") or ""),
                         "correction": str(v.get("correction") or "")}
                        for v in data if isinstance(v, dict)]
            if not verdicts:
                raise LLMError("reviewer produced no verdicts")
            verification = ("failed" if any(v["verdict"] == "wrong" for v in verdicts)
                            else "passed")
        except Exception as e:      # CancelledError is not an Exception
            logger.exception("acceptance review of run %s failed", run_id)
            summary = "review failed: %s" % e
            self.db.log(key, "review_failed", {"error": str(e)})
        self.db.add_verification(run_id, root["id"], verdicts, summary, rnd)
        self.db.set_run_verification(run_id, verification)
        self.db.log(key, "review_done", {"verification": verification,
                                         "verdicts": len(verdicts)})
        self._publish_verifications(run_id)
        self._publish_run(run_id)
        return verification

    async def reverify(self, run_id: str) -> dict:
        """Re-run the acceptance review of a done plan run whose build sandbox
        is still around. ValueError (-> 400) names the reason otherwise."""
        run = self.db.get_run(run_id)
        if not run:
            raise ValueError("no such run")
        if not run.get("plan"):
            raise ValueError("run has no plan")
        if run["status"] != "done":
            raise ValueError("run is %s, only a done run can be re-verified" % run["status"])
        row = self.db.get_sandbox(run_id)
        if not row or row["status"] not in ("kept", "ready"):
            raise ValueError("the build sandbox is %s: nothing to review in"
                             % (row["status"] if row else "absent"))
        dep = self.db.get_deployment(run_id)
        if not dep or dep["status"] != "ready":
            raise ValueError("deployment is %s, it must be ready"
                             % (dep["status"] if dep else "absent"))
        if self._alive(run_id):
            raise ValueError("run is busy")
        if await self._attach_kept(run_id) is None and self._sandboxes.get(run_id) is None:
            raise ValueError("the build sandbox is gone (vm not reachable)")
        self.db.set_run_status(run_id, "verifying")
        self._publish_run(run_id)
        self._spawn(self._reverify(run_id, row), run_id)
        return self.db.get_run(run_id)

    async def _reverify(self, run_id: str, row: dict):
        try:
            sb = self._sandboxes.get(run_id)
            if sb is None:
                sb = self._sandbox_factory(run_id)
                sb.adopt(row)
                self._sandboxes[run_id] = sb
            if not await sb.alive():
                self._review_skipped(run_id, "build sandbox is gone")
            else:
                await self._review_with_fixes(run_id)
            await self._finish_review(run_id)
        except asyncio.CancelledError:
            self._mark_cancelled(run_id)
            await self._release_sandbox(run_id, keep=True)
            raise
        except Exception as e:
            logger.exception("re-verification of run %s failed", run_id)
            self.db.set_run_verification(run_id, "error")
            self.db.log("run:" + run_id, "review_failed", {"error": str(e)})
            await self._finish_review(run_id)

    async def _run_resume(self, run_id: str, root: dict):
        try:
            result, changed = await self._resume_task(root)
            # A code run's deliverable is the repo (exported by _integrate):
            # never LLM-render it.
            if (config.ENABLE_ARTIFACTS and not self.db.get_run(run_id).get("plan")
                    and (changed or not self.db.get_artifacts(run_id))):
                await self._render_artifacts(run_id, root, result)
            self.db.finish_run(run_id, "done", result=result)
            if self._wants_deploy(run_id):      # no `done` flicker: deploy follows
                self.db.set_run_status(run_id, "deploying")
        except Exception as e:
            logger.exception("resume of run %s failed", run_id)
            self.db.finish_run(run_id, "failed", error=str(e))
        self._publish_run(run_id)

    async def _resume_task(self, task: dict, force: bool = False) -> tuple[dict, bool]:
        """Returns (result, changed) — changed means work was redone at or
        below this node, so ancestors must re-synthesize. force: a sibling
        dependency was redone, so redo this node even if it was done."""
        task = self.db.get_task(task["id"])
        children = [c for c in self.db.get_children(task["id"])
                    if c["role"] != REVIEW_ROLE]
        if not children:
            if task["status"] == "done" and task["result"] and not force:
                return json.loads(task["result"]), False
            # Leaf (or never got past its decision call): execute fresh.
            self._update(task["id"], status="pending", error=None)
            return await self._execute(task), True
        # Children exist — the decision was already made; resume them, then
        # re-synthesize only if anything below actually changed.
        task_id = task["id"]
        try:
            async def run_one(child, dep_values):
                # A dependency that was redone forces its dependents to redo.
                force = any(v[1] for v in dep_values)
                return await self._resume_task(child, force=force)

            raw = await self._schedule(children, run_one)
            # raw values are (result, changed); skipped/failed are exceptions
            any_changed = any(isinstance(o, BaseException) or o[1]
                              for o in raw.values())
            outcomes = {k: (v if isinstance(v, BaseException) else v[0])
                        for k, v in raw.items()}
            succeeded, failed = self._partition(children, outcomes)
            self._fail_if_code_failed(task, failed)
            if not succeeded:
                raise LLMError("all %d subtasks failed" % len(children))
            if (not any_changed and not force and task["status"] == "done"
                    and task["result"]):
                return json.loads(task["result"]), False
            integration = None
            if task["parent_id"] is None and not failed:
                integration = await self._integrate_with_fixes(task["run_id"], task)
            self._update(task_id, status="synthesizing", error=None)
            self.db.log(task_id, "resume_resynthesis",
                        {"children": len(children), "failed": len(failed)})
            result, usage = await self._synthesize(task, succeeded, failed,
                                                   integration)
            self.db.add_tokens(task_id, usage["input_tokens"], usage["output_tokens"])
            self._add_spend(task["run_id"], task_id, "synthesize", usage)
            self._update(task_id, status="done", result=json.dumps(result),
                         finished_at=time.time())
            return result, True
        except Exception as e:
            self._update(task_id, status="failed", error=str(e),
                         finished_at=time.time())
            self.db.log(task_id, "failed", {"error": str(e)})
            raise

    async def verify(self, run_id: str) -> list:
        """Adversarial verification of a finished run: one refutation pass per
        top-level section (narrow context — only that section's output+facts),
        plus one cross-consistency pass over all sections' facts."""
        run = self.db.get_run(run_id)
        if not run or run["status"] != "done" or not run["result"]:
            raise ValueError("run has no finished result to verify")
        tasks = self.db.get_tasks(run_id)
        root = next(t for t in tasks if not t["parent_id"])
        sections = [t for t in tasks
                    if t["parent_id"] == root["id"] and t["status"] == "done"
                    and t["result"]]
        if not sections:
            sections = [root]

        self.db.clear_verifications(run_id)
        self.db.set_run_status(run_id, "verifying")
        self._publish_run(run_id)
        system = self.db.get_prompt("verifier") or ""
        tier = config.MODEL_TIERS[config.DEFAULT_TIER]  # verify stays deep
        try:
            async def check(section):
                r = json.loads(section["result"])
                prompt = ("# Section under verification: %s (role: %s)\n\n"
                          "## Output\n%s\n\n## Facts claimed\n%s"
                          % (section["title"], section["role"],
                             r.get("output", ""),
                             json.dumps(r.get("facts", []), indent=2)))
                async with self.semaphore:
                    verdicts, usage = await self.llm.verify(system, prompt, tier)
                self.db.add_tokens(root["id"], usage["input_tokens"],
                                   usage["output_tokens"])
                self._add_spend(run_id, root["id"], "verify", usage)
                self.db.add_verification(run_id, section["id"],
                                         verdicts.get("verdicts", []),
                                         verdicts.get("summary", ""))

            outcomes = await asyncio.gather(*(check(s) for s in sections),
                                            return_exceptions=True)
            for section, outcome in zip(sections, outcomes):
                if isinstance(outcome, BaseException):
                    logger.error("verify failed for %s: %s", section["id"], outcome)
                    self.db.add_verification(
                        run_id, section["id"], [],
                        "verification failed: %s" % outcome)

            if len(sections) > 1:
                cross_system = system + "\n\n" + (
                    self.db.get_prompt("verifier_cross") or "")
                allfacts = {
                    s["title"]: json.loads(s["result"]).get("facts", [])
                    for s in sections
                }
                prompt = ("# Facts from every section — check CROSS-SECTION "
                          "consistency only\n%s" % json.dumps(allfacts, indent=2))
                try:
                    async with self.semaphore:
                        verdicts, usage = await self.llm.verify(cross_system, prompt, tier)
                    self.db.add_tokens(root["id"], usage["input_tokens"],
                                       usage["output_tokens"])
                    self._add_spend(run_id, root["id"], "verify", usage)
                    self.db.add_verification(run_id, root["id"],
                                             verdicts.get("verdicts", []),
                                             verdicts.get("summary", ""))
                except Exception as e:
                    logger.error("cross-verify failed: %s", e)
                    self.db.add_verification(run_id, root["id"], [],
                                             "cross-verification failed: %s" % e)
        finally:
            self.db.set_run_status(run_id, "done")
            self._publish_run(run_id)
        result = self.db.get_verifications(run_id)
        self.bus.publish({"type": "verification", "run_id": run_id,
                          "verifications": result})
        return result

    async def rerender(self, run_id: str) -> list:
        """Re-run artifact rendering for a finished run (e.g. after a render
        failure). Returns the artifact rows."""
        run = self.db.get_run(run_id)
        if not run or run["status"] != "done" or not run["result"]:
            raise ValueError("run has no finished result to render")
        root = next(t for t in self.db.get_tasks(run_id) if not t["parent_id"])
        await self._render_artifacts(run_id, root, json.loads(run["result"]))
        self.db.set_run_status(run_id, "done")
        self._publish_run(run_id)
        return self.db.get_artifacts(run_id)

    async def _render_artifacts(self, run_id: str, root: dict, result: dict):
        """Best-effort: turn the finished deliverable into real files.
        A render failure never fails the run."""
        import os
        self.db.set_run_status(run_id, "rendering")
        self._publish_run(run_id)
        try:
            run = self.db.get_run(run_id)
            system = self.db.get_prompt("artifact_render") or ""
            prompt = ("# Original request\n%s\n\n# Deliverable\n%s\n\n# Facts\n%s"
                      % (run["request"], result.get("output", ""),
                         json.dumps(result.get("facts", []), indent=2)))
            dest = os.path.join(config.ARTIFACTS_DIR, run_id)
            tier = config.MODEL_TIERS["standard"]  # render stays standard
            async with self.semaphore:
                files, usage = await self.llm.render_artifacts(system, prompt, dest, tier)
            self.db.add_tokens(root["id"], usage["input_tokens"], usage["output_tokens"])
            self._add_spend(run_id, root["id"], "render", usage)
            for f in files:
                self.db.add_artifact(run_id, f["filename"], f["path"], f["bytes"])
            self.db.log(root["id"], "artifacts", {"files": [f["filename"] for f in files]})
            self.bus.publish({"type": "artifacts", "run_id": run_id,
                              "files": self.db.get_artifacts(run_id)})
        except Exception as e:
            logger.exception("artifact render failed for run %s", run_id)
            self.db.log(root["id"], "artifact_render_failed", {"error": str(e)})

    # -- task execution -----------------------------------------------------

    async def _execute(self, task: dict, dep_values=None) -> dict:
        """Execute one task node; returns its result dict. Raises on failure."""
        task_id = task["id"]
        try:
            self._update(task_id, status="running", started_at=time.time())
            self.db.log(task_id, "start", {"depth": task["depth"], "role": task["role"]})

            if task.get("kind") == "code":
                # No decompose decision for code tasks: the runner is the
                # whole execution.
                return await self._execute_code_task(task, dep_values)
            decision, usage = await self._decide(task)
            self.db.add_tokens(task_id, usage["input_tokens"], usage["output_tokens"])
            self._add_spend(task["run_id"], task_id, "decide", usage)
            self._update(task_id, action=decision["action"], model=usage["model"])
            self.db.log(task_id, "decision", {"action": decision["action"],
                                              "reasoning": decision.get("reasoning")})

            if decision["action"] == "complete":
                result = decision.get("result") or {"summary": "", "output": ""}
                self._update(task_id, status="done", result=json.dumps(result),
                             finished_at=time.time())
                return result

            return await self._decompose(task, decision)
        except Exception as e:
            self._update(task_id, status="failed", error=str(e),
                         finished_at=time.time())
            self.db.log(task_id, "failed", {"error": str(e)})
            raise

    # -- coding leaves (task 07c) --------------------------------------------

    def _merge_lock(self, run_id: str) -> asyncio.Lock:
        return self._merge_locks.setdefault(run_id, asyncio.Lock())

    @staticmethod
    def _tail(text: str, n: int = 1500) -> str:
        return (text or "")[-n:]

    async def _init_workspace(self, run_id: str):
        """Create (or keep, on resume) <workdir>/repo with a scaffold commit
        and an `integration` branch, and install the runner + API key."""
        run = self.db.get_run(run_id)
        plan = run.get("plan")
        sb = self._sandboxes.get(run_id)
        if not plan or sb is None:
            return
        wd = sb.workdir
        repo = "%s/repo" % wd
        key = "run:" + run_id
        if config.ANTHROPIC_API_KEY:
            # secret-env path: sandbox.run writes <workdir>/.env (mode 600)
            await sb.run("true", env={"ANTHROPIC_API_KEY": config.ANTHROPIC_API_KEY})
        elif sb.kind != "mock":
            raise ScaffoldError("workspace_init_failed: ANTHROPIC_API_KEY is not set")
        with open(os.path.join(RUNNER_DIR, sb.runner_script), "rb") as f:
            await sb.put("%s/runner.py" % wd, f.read(), 0o755)
        r = await sb.run("test -d .git && git rev-parse --verify -q integration", cwd=repo)
        if r["rc"] == 0:
            # symlinked .venv/node_modules need the bare (no trailing slash) patterns
            await sb.run("for p in .venv node_modules; do grep -qxF \"$p\" .git/info/exclude"
                         " || printf '%s\\n' \"$p\" >> .git/info/exclude; done", cwd=repo)
            self.db.log(key, "workspace_reused", {"repo": repo})
            return
        q = shlex.quote
        bundle = self._seed_bundle(run)
        parent = run.get("parent_run_id")
        if bundle:
            bad = [c for c in plan.get("scaffold_commands") or []
                   if re.search(r"(^|&&|;|\|\|)\s*(git\s+(init|checkout|clone)\b|npm\s+init\b)", c)]
            if bad:
                self.db.log(key, "scaffold_filtered", {"cmds": bad})
                raise ScaffoldError("scaffold_failed: '%s' is not allowed on a seeded workspace"
                                    % bad[0])
        elif parent:
            self.db.log(key, "workspace_seed_missing", {
                "parent": parent,
                "bundle": os.path.join(config.ARTIFACTS_DIR, parent, "repo-%s.bundle" % parent)})
        excl = "printf '.pipez/\\n.venv\\nnode_modules\\n__pycache__/\\n.pytest_cache/\\n' >> .git/info/exclude"
        cfg = "git config user.name pipez && git config user.email pipez@local"
        mk = "rm -rf %s && mkdir -p %s %s/logs %s/wt" % (q(repo), q(repo), q(wd), q(wd))
        if bundle:
            with open(bundle, "rb") as f:
                data = f.read()
            steps = [(mk, wd)]
            await self._run_steps(sb, steps)
            await sb.put("%s/parent.bundle" % wd, data)
            await self._run_steps(sb, [
                ("git clone -q %s %s" % (q("%s/parent.bundle" % wd), q(repo)), wd),
                ("git checkout -q main || true", repo),
                (cfg, repo), (excl, repo)])
            h = await sb.run("git rev-parse HEAD", cwd=repo)
            self.db.log(key, "workspace_seeded", {
                "parent": parent, "bundle": os.path.basename(bundle),
                "head": h["stdout"].strip()})
        else:
            await self._run_steps(sb, [(mk, wd), ("git init -q -b main", repo),
                                       (cfg, repo), (excl, repo)])
        for cmd in plan.get("scaffold_commands") or []:
            r = await sb.run(cmd, cwd=repo, timeout=600)
            self.db.log(key, "scaffold", {"cmd": cmd, "rc": r["rc"], "seconds": r["seconds"],
                                          "tail": self._tail(r["stdout"] + r["stderr"])})
            if r["rc"]:
                self.db.log(key, "scaffold_failed", {"cmd": cmd, "rc": r["rc"]})
                raise ScaffoldError("scaffold_failed: `%s` exited %s: %s" % (
                    cmd, r["rc"], self._tail(r["stderr"] or r["stdout"], 400)))
        r = await sb.run("test -f .gitignore", cwd=repo)
        if r["rc"]:     # the scaffold did not create one: write the baseline
            await sb.put("%s/.gitignore" % repo, BASELINE_GITIGNORE)
            self.db.log(key, "gitignore_baseline", {})
        msg = "scaffold (child of %s)" % parent if bundle else "scaffold"
        await self._run_steps(sb, [
            ("git add -A", repo),
            ("git commit -q --allow-empty -m %s" % q(msg), repo),
            ("git checkout -q -B integration" if bundle else "git branch integration", repo)]
            + ([] if bundle else [("git checkout -q integration", repo)]))

    async def _run_steps(self, sb, steps):
        for cmd, cwd in steps:
            r = await sb.run(cmd, cwd=cwd)
            if r["rc"]:
                raise ScaffoldError("scaffold_failed: %s: %s" % (cmd, self._tail(r["stderr"])))

    def _leaf_model(self, task: dict) -> str:
        name = task.get("tier") or ""
        if name == "local":     # code tasks never run on local
            name = "light"
        tier = config.MODEL_TIERS.get(name) or config.MODEL_TIERS["standard"]
        if tier.get("provider") != "anthropic":
            tier = config.MODEL_TIERS["standard"]
        return tier["model"]

    @staticmethod
    def _human_answers_md(task: dict) -> str:
        q = task.get("question") or {}
        items = list(q.get("previous") or [])
        if q.get("answer"):
            items.append({"key": q.get("key"), "question": q.get("question"),
                          "value": q["answer"].get("value"),
                          "reason": q["answer"].get("reason")})
        if not items:
            return ""
        out = ["\n# Human answer\nThe human answered your question(s); these answers "
               "are binding. Apply them and finish the task."]
        for it in items:
            out.append("- Q (%s): %s\n  A: %s%s" % (
                it.get("key"), it.get("question"), it.get("value"),
                "\n  Reason: %s" % it["reason"] if it.get("reason") else ""))
        return "\n".join(out) + "\n"

    def _task_md(self, task: dict, run: dict, plan_task: dict, module: dict,
                 failure: str = None) -> str:
        plan = run["plan"]
        deps = []
        for d in task.get("depends_on") or []:
            dt = self.db.get_task(d)
            res = json.loads(dt["result"]) if dt and dt.get("result") else {}
            deps.append("- **%s** (module %s): %s" % (
                dt["title"] if dt else d, dt.get("module") if dt else "",
                (res.get("summary") or "(no summary)").replace("\n", " ")))
        accept = "\n".join("- [ ] %s" % a for a in task.get("acceptance") or []) or "- [ ] (none given)"
        return """# Task: {title}

Plan task id: {pid}
Task id: {tid}
Module: {mod}
Module path: {mpath}

## Brief (binding)
{brief}

## Architecture
{arch}

## Repo layout
{layout}

## Module contract ({mod})
{contract}

## Your task
{prompt}

## Acceptance
{accept}

## Dependencies already merged into this branch
{deps}

## Environment
- Build command: `{build_cmd}`
- Test command: `{test_cmd}`
- The repo's virtualenv is at `./.venv` in this worktree: use `.venv/bin/python` and
  `.venv/bin/pip`, never system `python3`.
- The runner re-runs `{test_cmd}` from the worktree root after you finish; your `tests`
  field in result.json is advisory.

## Runner contract
- Work only inside this worktree (your current directory); never touch other paths.
- Keep the public interface exactly as in the module contract.
- Add or keep tests runnable with the repo's standard command; they must pass.
- Commit ALL work on this branch with a meaningful commit message. Leave nothing uncommitted.
- Finally write `.pipez/result.json` (not committed) as JSON:
  {{"summary": str, "output": str (markdown: what was built, how to run the tests),
   "facts": [{{"claim": str, "value": str, "source": "<path or test name>"}}],
   "tests": {{"command": str, "passed": int, "failed": int}}}}
- Ask a question ONLY when the task cannot be completed without a human choice that
  the brief does not settle. Then write `.pipez/question.json` INSTEAD of result.json
  (not committed) and stop:
  {{"key": str (dotted decision key, e.g. "auth.provider"), "question": str,
   "why_it_matters": str,
   "options": [{{"value": str, "label": str, "consequences": str}}],
   "proposed_default": str}}
  Do NOT ask about anything an engineer should decide (naming, structure, libraries
  within the stack, implementation details): decide those yourself. If this file ends
  with a "Human answer" section, your question was already answered: apply it and finish.
""".format(title=task["title"], pid=plan_task.get("id", ""), tid=task["id"],
           mod=task.get("module") or "", mpath=module.get("path", ""),
           brief=run.get("brief") or "", arch=plan.get("architecture", ""),
           layout=plan.get("repo_layout", ""), contract=module.get("contract", ""),
           prompt=task["prompt"], accept=accept,
           build_cmd=plan.get("build_command") or "(none given)",
           test_cmd=plan.get("test_command") or "(none given)",
           deps="\n".join(deps) or "- none"
           ) + self._human_answers_md(task) + (
        "\n# Previous attempt failed\nYour previous attempt at this task failed. "
        "Fix the cause (tail of the failure below), then finish the task as "
        "described above.\n```\n%s\n```\n" % failure[-2000:] if failure else "")

    def _review_md(self, task: dict, run: dict, failure: str = None) -> str:
        plan = run["plan"]
        dep = self.db.get_deployment(run["id"]) or {}
        spec = plan.get("deploy") or {}
        n, crit = 0, []
        for pt in plan.get("tasks") or []:
            for a in pt.get("acceptance") or []:
                n += 1
                crit.append("%d. [%s] %s" % (n, pt.get("title") or pt.get("id") or "", a))
        return """# Task: Acceptance review

Task id: {tid}

## Brief (binding)
{brief}

## Architecture
{arch}

## Acceptance criteria (every one needs a verdict; the claim is the criterion)
{crit}

## Deployed app
- URL: {url}
- Health path: `{health}`

## Environment
- Test command: `{test_cmd}`
- This worktree is a detached checkout of the finished `integration` branch; the repo's
  virtualenv is at `./.venv` (use `.venv/bin/python`, never system `python3`).
- The deployed app is reachable from this machine at the URL above.

## Rules
{rules}

## Output
Write `.pipez/verdicts.json` (not committed) as JSON, instead of result.json:
  {{"summary": str, "ok": bool, "verdicts": [{{"claim": str (the criterion, verbatim, or
   "brief: <decision>"), "verdict": "confirmed"|"unverifiable"|"wrong", "note": str,
   "correction": str (required, concrete, when wrong; otherwise "")}}]}}
Do not modify any other file in the worktree.
""".format(tid=task["id"], brief=run.get("brief") or "", arch=plan.get("architecture", ""),
           crit="\n".join(crit) or "(the plan lists no acceptance criteria)",
           url=dep.get("url") or "(none)", health=spec.get("health_path") or "/",
           test_cmd=plan.get("test_command") or "(none given)",
           rules=task.get("prompt") or "") + (
        "\n# Previous attempt failed\n```\n%s\n```\n" % failure[-2000:] if failure else "")

    async def _merge_leaf(self, task: dict, sb, repo: str, branch: str, direct: bool,
                          out: dict):
        """Merge a finished leaf into `integration` (caller holds the merge lock).
        None on success; on a content conflict the merge is aborted and
        {"error", "note"} returned so the caller can retry on the new integration."""
        if direct:
            r = await sb.git(["merge", "--ff-only", out["commit"]], cwd=repo)
            if r["rc"]:
                raise LLMError("fix_merge_failed: %s" % self._tail(r["stderr"] or r["stdout"], 400))
            return None
        r = await sb.git(["merge", "--no-ff", branch, "-m",
                          "merge %s: %s" % (task["id"], task["title"])], cwd=repo)
        if not r["rc"]:
            return None
        u = await sb.git(["diff", "--name-only", "--diff-filter=U"], cwd=repo)
        files = [f for f in u["stdout"].splitlines() if f.strip()]
        await sb.git(["merge", "--abort"], cwd=repo)
        self.db.log(task["id"], "merge_conflict", {"files": files, "branch": branch})
        listed = ", ".join(files) or self._tail(r["stderr"])
        return {"error": "merge_conflict: %s" % listed,
                "note": "Your previous attempt conflicted with work merged by a sibling "
                        "task on: %s. The worktree now contains the merged versions - "
                        "re-apply your changes on top of them, do not overwrite unrelated "
                        "content." % listed}

    async def _kill_runner(self, sb, task_id: str = None):
        """Best-effort remote kill of a runner's process group, shielded so a
        cancelled caller still completes it."""
        async def go():
            try:
                await sb.kill_runner(task_id)
            except Exception as e:  # noqa: BLE001
                logger.warning("remote runner kill (%s) failed: %s", task_id or "all", e)
        fut = asyncio.ensure_future(go())
        self._kills.add(fut)
        fut.add_done_callback(self._kills.discard)
        try:
            await asyncio.shield(fut)
        except asyncio.CancelledError:
            pass        # cancelled again: the kill still runs to completion

    async def _diagnose_lost_runner(self, sb, log_path: str, rc) -> str:
        """Error text for a runner that produced no result: the last Bash commands
        from the tail of its log, and a hint when one was a broad kill."""
        why = "runner produced no result (rc=%s)" % rc
        try:
            tail = (await sb.get(log_path))[-3072:].decode(errors="replace")
        except Exception:  # noqa: BLE001 - diagnosis is best effort
            return why
        cmds = self._bash_commands(tail)[-3:]
        if not cmds:
            return why
        why = "runner connection lost (rc=%s); last commands: %s" % (
            rc, " | ".join(c[:200] for c in cmds))
        if any(re.search(r"\b(pkill|killall)\b", c) for c in cmds):
            why += " - a broad kill terminated the runner"
        return why

    @staticmethod
    def _bash_commands(text: str) -> list:
        """Bash tool commands in a runner-log tail (JSON lines whose content blocks
        are dicts or their repr strings; the first line may be cut off)."""
        cmds = []
        for line in text.splitlines():
            try:
                msg = json.loads(line)
            except ValueError:
                msg = None
            blocks = msg.get("content") if isinstance(msg, dict) else None
            found = False
            for b in blocks if isinstance(blocks, list) else []:
                if isinstance(b, dict) and b.get("name") == "Bash" \
                        and isinstance(b.get("input"), dict):
                    cmds.append(str(b["input"].get("command") or ""))
                    found = True
                elif isinstance(b, str):
                    for m in re.finditer(BASH_REPR, b):
                        try:
                            cmds.append(ast.literal_eval(m.group(1)))
                            found = True
                        except (ValueError, SyntaxError):
                            pass
            if not found and msg is None:       # cut-off first line: scan the raw text
                for m in BASH_REPR.finditer(line):
                    cmds.append(m.group(1)[1:-1])
        return [c for c in cmds if c.strip()]

    async def _execute_code_task(self, task: dict, dep_values=None) -> dict:
        """Run one code task in its own worktree off `integration`, then merge
        its branch back under the run's merge lock. Dependency summaries come
        from the stored results of task['depends_on'] (dep_values is accepted
        for the scheduler's signature; resume passes tuples, so the DB is the
        one source).

        The runner may fail (retried LEAF_RETRIES times, with the failure tail
        in task.md) or ask a human question (the task parks as awaiting_human,
        then re-runs in a fresh worktree with the answer). A fixer task
        (module "*") works on a detached worktree at `integration` and the
        result is fast-forwarded into that branch: no merge commit."""
        task_id, run_id = task["id"], task["run_id"]
        sb = self._sandboxes.get(run_id)
        run = self.db.get_run(run_id)
        if sb is None or not run.get("plan"):
            raise LLMError("code task needs a planned run with a sandbox")
        plan = run["plan"]
        wd, repo = sb.workdir, "%s/repo" % sb.workdir
        wt, branch = "%s/wt/%s" % (wd, task_id), "task/%s" % task_id
        q = shlex.quote
        direct = task.get("module") == "*"
        review = task.get("role") == REVIEW_ROLE
        plan_tasks = plan.get("tasks") or []
        if direct:
            plan_task = {"id": "fix%d" % task["seq"]}
            module = {"path": ".", "contract": "Whole repository: keep every "
                      "module's public contract unchanged."}
        else:
            plan_task = plan_tasks[task["seq"]] if task["seq"] < len(plan_tasks) else {}
            module = next((m for m in plan.get("modules") or []
                           if m.get("name") == task.get("module")), {})
        model = self._leaf_model(task)
        self._update(task_id, model=model)

        failure, retries_left, asks = None, max(config.LEAF_RETRIES, 0), 0
        conflict_retries = 1     # one extra retry, separate from the test-failure budget
        while True:
            async with self._merge_lock(run_id):
                # fresh worktree off the current integration (also the resume,
                # retry and answered-question path)
                await sb.git(["worktree", "remove", "--force", wt], cwd=repo)
                await sb.run("rm -rf %s" % q(wt))
                await sb.git(["worktree", "prune"], cwd=repo)
                await sb.git(["branch", "-D", branch], cwd=repo)
                add = ["worktree", "add", wt, "--detach", "integration"] if direct \
                    else ["worktree", "add", wt, "-b", branch, "integration"]
                r = await sb.git(add, cwd=repo)
                if r["rc"]:
                    raise LLMError("worktree_failed: %s" % self._tail(r["stderr"]))
                for dep in (".venv", "node_modules"):    # both are in .git/info/exclude
                    src, dst = "%s/%s" % (repo, dep), "%s/%s" % (wt, dep)
                    await sb.run("[ -e %s ] && [ ! -e %s ] && [ ! -L %s ] && ln -s %s %s; true"
                                 % (q(src), q(dst), q(dst), q(src), q(dst)))
            self.db.log(task_id, "worktree", {"path": wt, "branch": None if direct else branch})
            if review:
                md = self._review_md(self.db.get_task(task_id), run, failure)
            else:
                md = self._task_md(self.db.get_task(task_id), run, plan_task, module, failure)
            await sb.put("%s/.pipez/task.md" % wt, md)

            settings = {"test_command": str(plan.get("test_command") or "").strip() or None,
                        "review": bool(review)}
            await sb.put("%s/.pipez/runner.json" % wt, json.dumps(settings))
            cmd = ("%s %s --cwd %s --task-file .pipez/task.md --model %s --max-turns %d "
                   "--max-budget-usd %s --log %s" % (
                       sb.python, q("%s/runner.py" % wd), q(wt), q(model),
                       config.LEAF_MAX_TURNS, config.LEAF_MAX_BUDGET_USD,
                       q("%s/logs/%s.jsonl" % (wd, task_id))))
            env = {"ANTHROPIC_API_KEY": config.ANTHROPIC_API_KEY} if config.ANTHROPIC_API_KEY else None
            # Start the runner in its own session and record the group id, so a
            # stop (or a restart) can kill it remotely: killing the local ssh
            # alone leaves the agent running in the VM.
            pidf = q("%s/run/%s.pid" % (wd, task_id))
            wrapped = ("mkdir -p %s; setsid %s & p=$!; echo $p > %s; wait $p; rc=$?; "
                       "rm -f %s; exit $rc" % (q("%s/run" % wd), cmd, pidf, pidf))
            try:
                async with self.semaphore:
                    r = await sb.run(wrapped, cwd=wt, env=env, timeout=config.LEAF_TIMEOUT)
            except asyncio.CancelledError:
                await self._kill_runner(sb, task_id)
                raise
            if r["rc"] == 124:          # timed out: the remote group outlives the ssh
                await self._kill_runner(sb, task_id)
            out = None
            for line in reversed((r["stdout"] or "").splitlines()):
                try:
                    cand = json.loads(line)
                except ValueError:
                    continue
                if isinstance(cand, dict):
                    out = cand
                    break

            err = None
            if out is None:
                if r["rc"] == 124:
                    why = "runner timed out"
                else:
                    why = await self._diagnose_lost_runner(
                        sb, "%s/logs/%s.jsonl" % (wd, task_id), r["rc"])
                err = "%s: %s" % (why, self._tail(r["stderr"], 800))
            else:
                self.db.add_tokens(task_id, int(out.get("input_tokens") or 0),
                                   int(out.get("output_tokens") or 0))
                self.db.add_cost(task_id, out.get("cost_usd") or 0)
                self._add_spend(run_id, task_id,
                            "review" if review else "fix" if direct else "leaf",
                            {"model": model,
                             "input_tokens": out.get("input_tokens"),
                             "output_tokens": out.get("output_tokens")},
                            source="reported", cost=float(out.get("cost_usd") or 0))
                tests = out.get("tests")
                self.db.log(task_id, "runner", {k: out.get(k) for k in (
                    "ok", "commit", "files_changed", "tests", "cost_usd", "num_turns",
                    "is_error", "error", "needs_human")})
                if review:
                    tests = None        # the reviewer reports; nothing is gated on tests
                if isinstance(out.get("needs_human"), dict) and not out.get("ok"):
                    asks += 1
                    if asks > MAX_TASK_QUESTIONS:
                        raise LLMError("code task asked more than %d questions"
                                       % MAX_TASK_QUESTIONS)
                    await self._ask_human(task, out["needs_human"])
                    continue          # answered: not a failed attempt
                if not out.get("ok") or out.get("is_error"):
                    err = "code task failed: %s" % (out.get("error") or "runner reported ok=false")
                elif review:
                    pass
                elif config.LEAF_REQUIRE_TESTS and not tests:
                    err = "tests missing: runner reported no test results (tests: null)"
                elif config.LEAF_REQUIRE_TESTS and (tests.get("failed") or 0) > 0:
                    err = "tests failed (%s failed, rc=%s): %s" % (
                        tests.get("failed"), tests.get("rc"),
                        self._tail(tests.get("tail") or "", 1000))
            if err is None:
                if review:
                    break
                async with self._merge_lock(run_id):
                    conflict = await self._merge_leaf(task, sb, repo, branch, direct, out)
                if conflict is None:
                    break
                if conflict_retries <= 0:
                    raise LLMError(conflict["error"])
                conflict_retries -= 1
                failure = conflict["note"]
                self.db.log(task_id, "leaf_retry", {"error": conflict["error"][:300],
                                                    "tail": failure, "conflict": True,
                                                    "retries_left": retries_left})
                continue
            if retries_left <= 0:
                raise LLMError(err)
            retries_left -= 1
            failure = self._tail(err, 2000)
            self.db.log(task_id, "leaf_retry", {"error": err[:300], "tail": failure,
                                                "retries_left": retries_left})

        if review:      # read-only: nothing to merge
            result = {"summary": out.get("summary") or "", "output": "", "facts": [],
                      "verdicts": (out.get("verdicts") or {}).get("verdicts") or [],
                      "metadata": {"cost_usd": out.get("cost_usd"),
                                   "num_turns": out.get("num_turns")}}
            self._update(task_id, status="done", result=json.dumps(result),
                         finished_at=time.time())
            return result

        result = {
            "summary": out.get("summary") or "", "output": out.get("output") or "",
            "facts": out.get("facts") or [],
            "metadata": {"files_changed": out.get("files_changed") or [],
                         "commit": out.get("commit"), "tests": out.get("tests"),
                         "cost_usd": out.get("cost_usd"), "num_turns": out.get("num_turns")},
        }
        self._update(task_id, status="done", result=json.dumps(result),
                     finished_at=time.time())
        return result

    async def _decide(self, task: dict):
        system = self._system_prompt(task)
        prompt = self._task_prompt(task)
        return await self._llm_call(
            task, lambda tier: self.llm.decide(system, prompt, task["depth"], tier))

    async def _decompose(self, task: dict, decision: dict) -> dict:
        task_id = task["id"]
        subtasks = decision.get("subtasks") or []
        if not subtasks:
            raise LLMError("decompose decision with no subtasks")

        if len(subtasks) > config.MAX_CHILDREN:
            self.db.log(task_id, "children_truncated",
                        {"requested": len(subtasks), "kept": config.MAX_CHILDREN})
            subtasks = subtasks[:config.MAX_CHILDREN]

        used = self.db.count_run_tasks(task["run_id"])
        room = config.MAX_TASKS_PER_RUN - used
        if len(subtasks) > room:
            self.db.log(task_id, "run_task_cap",
                        {"requested": len(subtasks), "kept": max(room, 0)})
            subtasks = subtasks[:max(room, 0)]
            if not subtasks:
                raise LLMError("run task cap reached (%d)" % config.MAX_TASKS_PER_RUN)

        shared_context = decision.get("shared_context") or None
        dep_idx = self._validate_deps(task_id, subtasks)
        children = [
            self.db.create_task(
                run_id=task["run_id"], parent_id=task_id, depth=task["depth"] + 1,
                seq=i, role=sub["role"], title=sub["title"], prompt=sub["prompt"],
                context=shared_context,
                tier=(sub.get("tier") if sub.get("tier") in config.MODEL_TIERS
                      else "deep"),
                effort=(sub.get("effort") if sub.get("effort") in EFFORTS else None),
            )
            for i, sub in enumerate(subtasks)
        ]
        for i, child in enumerate(children):
            if dep_idx[i]:
                children[i] = self.db.update_task(
                    child["id"], depends_on=[children[j]["id"] for j in dep_idx[i]])
        for child in children:
            self._publish_task(child)
        self._update(task_id, status="awaiting_children")

        async def run_one(child, dep_values):
            return await self._execute(child, dep_values)

        outcomes = await self._schedule(children, run_one)
        succeeded, failed = self._partition(children, outcomes)
        self._fail_if_code_failed(task, failed)
        if not succeeded:
            raise LLMError("all %d subtasks failed" % len(children))

        self._update(task_id, status="synthesizing")
        result, usage = await self._synthesize(task, succeeded, failed)
        self.db.add_tokens(task_id, usage["input_tokens"], usage["output_tokens"])
        self._add_spend(task["run_id"], task_id, "synthesize", usage)
        self._update(task_id, status="done", result=json.dumps(result),
                     finished_at=time.time())
        return result

    def _validate_deps(self, task_id: str, subtasks: list) -> list:
        """Per-subtask lists of valid sibling indices. Self-references,
        out-of-range indices and cycle-closing edges are dropped (logged as
        `deps_invalid`); a bad edge never fails the decomposition."""
        n = len(subtasks)
        deps, invalid = [], []
        for i, sub in enumerate(subtasks):
            kept = []
            raw = sub.get("depends_on") or []
            for j in (raw if isinstance(raw, list) else []):
                if isinstance(j, bool) or not isinstance(j, int):
                    invalid.append({"subtask": i, "dep": j, "reason": "not an integer"})
                elif j == i:
                    invalid.append({"subtask": i, "dep": j, "reason": "self-reference"})
                elif not 0 <= j < n:
                    invalid.append({"subtask": i, "dep": j, "reason": "out of range"})
                elif j not in kept:
                    kept.append(j)
            deps.append(kept)
        # Cycle removal (shared with plan validation).
        for node, j in self._break_cycles(dict(enumerate(deps))):
            invalid.append({"subtask": node, "dep": j, "reason": "cycle"})
        if invalid:
            self.db.log(task_id, "deps_invalid", {"dropped": invalid})
        return deps

    async def _schedule(self, children: list, run_one) -> dict:
        """DAG scheduler over sibling tasks. Starts every pending child whose
        dependencies are all done, waits for the first completion, marks
        transitive dependents of a failure `skipped`, repeats until nothing is
        pending or running. run_one(child, dep_values) -> value (or raises);
        returns {child_id: value | BaseException}. No deps -> all start at once."""
        by_id = {c["id"]: c for c in children}
        deps = {c["id"]: [d for d in (c.get("depends_on") or []) if d in by_id]
                for c in children}
        pending = list(children)
        running: dict = {}
        outcomes: dict = {}
        bad: set = set()  # failed or skipped ids
        try:
            while pending or running:
                # skipped: dependents of failed/skipped tasks, transitively
                changed = True
                while changed:
                    changed = False
                    for c in list(pending):
                        broken = next((d for d in deps[c["id"]] if d in bad), None)
                        if broken is None:
                            continue
                        pending.remove(c)
                        msg = "dependency failed: %s" % by_id[broken]["title"]
                        self._update(c["id"], status="skipped", error=msg,
                                     finished_at=time.time())
                        self.db.log(c["id"], "skipped",
                                    {"dependency": by_id[broken]["title"],
                                     "dependency_id": broken})
                        outcomes[c["id"]] = SkippedError(msg)
                        bad.add(c["id"])
                        changed = True
                for c in list(pending):
                    if all(d in outcomes and d not in bad for d in deps[c["id"]]):
                        pending.remove(c)
                        vals = [outcomes[d] for d in deps[c["id"]]]
                        running[asyncio.ensure_future(run_one(c, vals))] = c
                if not running:
                    break
                finished, _ = await asyncio.wait(
                    running, return_when=asyncio.FIRST_COMPLETED)
                for t in finished:
                    c = running.pop(t)
                    try:
                        outcomes[c["id"]] = t.result()
                    except BaseException as e:  # noqa: BLE001 - recorded per child
                        outcomes[c["id"]] = e
                        bad.add(c["id"])
        finally:
            for t in running:
                t.cancel()
            if running:     # let them unwind (kill runners) before we return
                await asyncio.gather(*running, return_exceptions=True)
        return outcomes

    def _partition(self, children: list, outcomes: dict):
        """Split children into succeeded / failed synthesis entries (skipped
        children land in failed with their error). Entries carry the titles
        of the siblings they depend on."""
        titles = {c["id"]: c["title"] for c in children}
        succeeded, failed = [], []
        for child in children:
            outcome = outcomes.get(child["id"])
            entry = {"role": child["role"], "title": child["title"]}
            dep_titles = [titles[d] for d in child.get("depends_on") or []
                          if d in titles]
            if dep_titles:
                entry["depends_on"] = dep_titles
            if isinstance(outcome, BaseException):  # includes SkippedError
                entry["error"] = str(outcome)
                failed.append(entry)
            else:
                entry["result"] = outcome
                succeeded.append(entry)
        return succeeded, failed

    def _fail_if_code_failed(self, task: dict, failed: list):
        """The root of a plan run with failed/skipped children must not
        synthesise (the LLM would invent work): fail the run, keep the sandbox,
        let resume redo only the non-done leaves."""
        if failed and task["parent_id"] is None and self.db.get_run(task["run_id"]).get("plan"):
            raise LLMError("%d code tasks failed: %s" % (
                len(failed), "; ".join(f["title"] for f in failed)))

    async def _synthesize(self, task: dict, succeeded: list, failed: list,
                          integration: dict = None):
        system = "%s\n\n%s" % (
            self._role_prompt(task),
            self.db.get_prompt("synthesis_default") or "",
        )
        run = self.db.get_run(task["run_id"])
        parts = []
        if run.get("context"):
            parts.append("# Project context (binding — supplied by the requester)\n%s"
                         % run["context"])
        parts.append("# Your task\n%s" % task["prompt"])
        if task["context"] and task["context"] != run.get("brief"):
            parts.append("# Shared context you received\n%s" % task["context"])
        parts.append("# Subtask results\n%s" % json.dumps(succeeded, indent=2))
        if run.get("plan"):
            parts.append("# Limits\nYou cannot create or modify files. Describe only "
                         "what the integration section below proves was built; do not "
                         "claim any work it does not show.")
        if failed:
            parts.append("# Failed subtasks\n%s" % json.dumps(failed, indent=2))
        if integration:
            parts.append(self._integration_section(integration))
        prompt = "\n\n".join(parts)
        return await self._llm_call(
            task, lambda tier: self.llm.synthesize(system, prompt, tier))

    # -- integration (task 07d) ------------------------------------------------

    @staticmethod
    def _integration_section(info: dict) -> str:
        lines = ["# Integration",
                 "All task branches are merged into `integration`; the build and "
                 "tests below ran on it and passed. Describe what was built and "
                 "exactly how to run it (this becomes the run's output).",
                 "- files changed: %d" % info["files_changed"],
                 "- tag: %s" % info["tag"]]
        for step in info["steps"]:
            lines.append("- %s: `%s` rc=%s (%.1fs)\n```\n%s\n```" % (
                step["step"], step["cmd"], step["rc"], step["seconds"], step["tail"]))
        return "\n".join(lines)

    async def _integrate_with_fixes(self, run_id: str, root: dict):
        """_integrate; on a fixable failure run a fixer leaf directly on
        `integration` and try again, up to PIPEZ_FIX_ROUNDS times. A failed
        fixer or exhausted rounds re-raise the integration failure."""
        rounds = 0
        while True:
            try:
                return await self._integrate(run_id, root)
            except IntegrationError as e:
                if not e.fixable or rounds >= config.FIX_ROUNDS:
                    raise
                rounds += 1
                kids = self.db.get_children(root["id"])
                n = sum(1 for c in kids if c.get("module") == "*") + 1
                prompt = (
                    "The integration of this repository failed.\n\n"
                    "Failing command: `%s` (exit code %s)\n\nOutput tail:\n```\n%s\n```\n\n"
                    "Make the full build and test pass; change as little as possible; "
                    "do not alter public contracts."
                    % (e.cmd, e.rc, self._tail(e.tail or str(e), 1500)))
                fix = self.db.create_task(
                    run_id=run_id, parent_id=root["id"], depth=1,
                    seq=max([c["seq"] for c in kids] or [-1]) + 1,
                    role="integrator-engineer", title="Fix integration (round %d)" % n,
                    prompt=prompt, tier="deep", kind="code", module="*",
                    acceptance=["The build and test commands pass on integration"])
                self._publish_task(fix)
                self.db.log(root["id"], "integrate_fix",
                            {"round": rounds, "task_id": fix["id"], "cmd": e.cmd})
                try:
                    await self._execute(fix)
                except Exception:
                    raise e

    async def _integrate(self, run_id: str, root: dict):
        """After every leaf merged: build + test on `integration`; on success
        fast-forward main, tag, export tar.gz + bundle as artifacts. Failure
        raises (root and run fail; the sandbox is kept by 07b policy)."""
        run = self.db.get_run(run_id)
        plan = run.get("plan")
        sb = self._sandboxes.get(run_id)
        if not plan or sb is None:
            return None
        rid, wd = run_id, sb.workdir
        repo, key = "%s/repo" % wd, root["id"]
        tag = "pipez/%s" % rid

        def fail(msg, fixable=False, cmd=None, rc=None, tail=None, **extra):
            self.db.log(key, "integrate_failed", dict(error=msg, **extra))
            raise IntegrationError(msg, fixable, cmd, rc, tail)

        r = await sb.git(["checkout", "-q", "integration"], cwd=repo)
        if r["rc"]:
            fail("integration failed: cannot checkout integration: %s" % self._tail(r["stderr"], 400))
        r = await sb.git(["status", "--porcelain"], cwd=repo)
        if r["stdout"].strip():
            fail("integration failed: dirty working tree on integration: %s"
                 % self._tail(r["stdout"], 400), fixable=True,
                 cmd="git status --porcelain", rc=0, tail=r["stdout"])
        steps = []
        for step, field in (("integrate_build", "build_command"),
                            ("integrate_test", "test_command")):
            cmd = (plan.get(field) or "").strip()
            if not cmd:
                continue
            r = await sb.run("bash -lc %s" % shlex.quote(cmd), cwd=repo,
                             timeout=config.INTEGRATE_TIMEOUT)
            entry = {"step": step.split("_")[1], "cmd": cmd, "rc": r["rc"],
                     "seconds": r["seconds"],
                     "tail": self._tail(r["stdout"] + r["stderr"])}
            self.db.log(key, step, {k: entry[k] for k in ("cmd", "rc", "seconds", "tail")})
            steps.append(entry)
            if r["rc"]:
                fail("integration failed: %s `%s` exited %s:\n%s" % (
                    entry["step"], cmd, r["rc"], entry["tail"]), step=step,
                    fixable=True, cmd=cmd, rc=r["rc"], tail=entry["tail"])
        r = await sb.git(["status", "--porcelain"], cwd=repo)
        if r["stdout"].strip():
            fail("integration failed: dirty working tree after build/test: %s"
                 % self._tail(r["stdout"], 400), fixable=True,
                 cmd="git status --porcelain", rc=0, tail=r["stdout"])

        # Pass: main <- integration (ff only), tag, export. Leave `integration`
        # checked out again so a resume keeps merging into it.
        tar = "repo-%s.tar.gz" % rid
        bundle = "repo-%s.bundle" % rid
        try:
            for args in (["checkout", "-q", "main"],
                         ["merge", "--ff-only", "integration"],
                         ["tag", "-f", tag],
                         ["archive", "--format=tar.gz", "-o", "%s/%s" % (wd, tar), "main"],
                         ["bundle", "create", "%s/%s" % (wd, bundle), "--all"]):
                r = await sb.git(args, cwd=repo)
                if r["rc"]:
                    fail("integration failed: git %s: %s" % (
                        " ".join(args[:2]), self._tail(r["stderr"] or r["stdout"], 400)))
        finally:
            await sb.git(["checkout", "-q", "integration"], cwd=repo)
        dest = os.path.join(config.ARTIFACTS_DIR, rid)
        os.makedirs(dest, exist_ok=True)
        for name in (tar, bundle):
            data = await sb.get("%s/%s" % (wd, name))
            path = os.path.join(dest, name)
            with open(path, "wb") as f:
                f.write(data)
            self.db.add_artifact(rid, name, path, len(data))
        files = set()
        for c in self.db.get_children(root["id"]):
            res = json.loads(c["result"]) if c.get("result") else {}
            files.update((res.get("metadata") or {}).get("files_changed") or [])
        self.db.log(key, "integrate_ok", {"tag": tag, "artifacts": [tar, bundle],
                                          "files_changed": len(files)})
        self.bus.publish({"type": "artifacts", "run_id": rid,
                          "files": self.db.get_artifacts(rid)})
        return {"steps": steps, "tag": tag, "files_changed": len(files)}

    # -- prompt assembly ----------------------------------------------------

    def _role_prompt(self, task: dict) -> str:
        return (self.db.get_prompt("role:%s" % task["role"])
                or self.db.get_prompt("system_default") or "")

    def _system_prompt(self, task: dict) -> str:
        system = self._role_prompt(task)
        if task["depth"] >= config.MAX_DEPTH - 1 or task.get("kind") == "code":
            system += "\n\n" + (self.db.get_prompt("leaf_note")
                                or "You must COMPLETE this task directly.")
        return system

    def _task_prompt(self, task: dict) -> str:
        run = self.db.get_run(task["run_id"])
        parts = []
        if run.get("context"):
            parts.append("# Project context (binding — supplied by the requester)\n%s"
                         % run["context"])
        if run.get("brief"):
            parts.append("# Project context (binding — the build brief)\n%s"
                         % run["brief"])
        if task["parent_id"]:
            parts.append("# Original request (top of the tree)\n%s" % run["request"])
            parts.append("# Ancestry\n%s" % " > ".join(self._ancestry(task)))
        for label, context in self._context_chain(task):
            if context == run.get("brief"):
                continue  # already injected above as binding project context
            parts.append("# Shared context from %s\n%s" % (label, context))
        parts.append("# Your task (role: %s) [depth:%d]\n%s"
                     % (task["role"], task["depth"], task["prompt"]))
        return "\n\n".join(parts)

    def _context_chain(self, task: dict) -> list:
        """Shared context from every ancestor level, outermost first —
        constraints set high in the tree reach the leaves."""
        chain, cur = [], task
        while cur:
            if cur["context"]:
                label = ("your parent" if cur["id"] == task["id"]
                         else "ancestor '%s'" % cur["role"])
                chain.append((label, cur["context"]))
            cur = self.db.get_task(cur["parent_id"]) if cur["parent_id"] else None
        return list(reversed(chain))

    def _ancestry(self, task: dict) -> list:
        chain, cur = [], task
        while cur["parent_id"]:
            cur = self.db.get_task(cur["parent_id"])
            chain.append("%s: %s" % (cur["role"], cur["title"]))
        return list(reversed(chain))

    # -- publishing ---------------------------------------------------------

    def _update(self, task_id, **fields):
        task = self.db.update_task(task_id, **fields)
        self._publish_task(task)

    def _publish_task(self, task: dict):
        self.bus.publish({"type": "task", "task": task})

    def _publish_run(self, run_id: str):
        self.bus.publish({"type": "run", "run": self.db.get_run(run_id)})
