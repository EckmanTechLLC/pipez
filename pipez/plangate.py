"""Mechanical plan gate: deterministic checks over a plan dict (no LLM, no I/O).

`check_plan(plan)` returns violations
`{"code", "severity" ("error"|"warn"), "subject", "message", "hint"}`;
`format_note(violations)` renders them as a revision note for the planner.
"""
import re

from pipez import config

# ---- moved from Engine: planner-rule heuristics (warnings only) -----------
# Phrases implying execution against a host (not mere mention of deploy).
_DEPLOY_RE = re.compile(
    r"\b(ssh|scp)\s+(to|into)?\s*\S*(vm|host|server)"
    r"|\b(live|real|remote)\s+(\w+\s+)?(vm|host|server|machine)\b"
    r"|\blive\s+(re)?deploy"
    r"|\bredeploy\b"
    r"|\bdeploy(s|ed|ing)?\s+(it\s+)?(to|on)\s+(the\s+|an?\s+)?(\w+\s+){0,3}(vm|host|server)\b"
    r"|\bon\s+the\s+(vm|host|server)\b"
    r"|\bsmoke[- ]?test(s|ing)?\s+(the\s+)?(live|deployed|running)\b", re.I)
# Tasks whose title starts with these verbs are file-authoring: skipped.
_AUTHOR_RE = re.compile(
    r"^\s*(write|author|add|create|update|document|readme)\b", re.I)
_PROCESS_RE = re.compile(
    r"\b(the report|build report|git diff|marked (as )?NOT RUN|"
    r"tool unavailable|shellcheck)\b", re.I)

# ---- patterns for the structural checks -----------------------------------
_EXT = (r"js|mjs|cjs|jsx|ts|tsx|py|html|css|json|md|sh|yml|yaml|toml|sql|txt|"
        r"service|go|rs|java|c|h|cpp|rb|php|lock|ini|cfg|env")
_PATH_RE = re.compile(r"[\w.\-]+/[\w./\-]+|\b[\w\-]+\.(?:%s)\b" % _EXT)
_CALL_RE = re.compile(r"\b([A-Za-z_$][\w$]*)\(")
_BACKTICK_RE = re.compile(r"`([^`\n]+)`")
_ROUTE_RE = re.compile(r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+/[\w/:.\-<>{}*]*")
_SHAPE_RE = re.compile(r"\{[^{}]*:[^{}]*\}")
_EXPORTS_LIST_RE = re.compile(
    r"\b(?:exports|emits|messages)\s*:|\bexports\s+(?!nothing\b|no\b)[A-Za-z_$]", re.I)
_NETWORK_RE = re.compile(
    r"\bweb ?sockets?\b|\bhttp/ws\b|\bhttp server\b|\broutes?\b|\bendpoints?\b",
    re.I)
_CODE_FILE_RE = re.compile(r"\.(?:js|mjs|cjs|jsx|ts|tsx|py|html|go|rs|java|rb|php)\b")
_MANUAL_RE = re.compile(r"manual|readme checklist|by hand", re.I)
_NOAUTO_RE = re.compile(r"no automated|not (directly )?(unit[- ])?tested", re.I)
_AUTOTEST_RE = re.compile(
    r"\bnpm (run )?test\b|node --test|pytest|\bunit tests?\b|\bintegration tests?\b"
    r"|\btests?/|\.test\.|\bsupertest\b|\bnode:test\b", re.I)
_HTTP_STATUS_RE = re.compile(r"\b[1-5]\d\d\b")
# Names too generic to prove a prompt knows a module's interface.
_STOP = {
    "require", "error", "parseint", "parsefloat", "date", "array", "object", "string",
    "number", "math", "json", "promise", "buffer", "function", "returns", "return",
    "test", "describe", "assert", "console", "express", "exports", "export", "import",
    "if", "for", "while", "new", "this", "true", "false", "null", "async", "await",
    "set", "map", "get", "post", "put", "delete", "url", "node", "type", "name",
    "close", "join", "open", "send", "init", "start", "stop", "run", "sweep", "emit",
}


_NONCODE = ("tests", "docs", "infra")


def _vtn(code, severity, subject, message, hint):
    return {"code": code, "severity": severity, "subject": str(subject),
            "message": message, "hint": hint}


def _symbols(text: str, files: bool = True) -> set:
    """Identifiers and files that identify an interface in `text`: backticked
    tokens, names followed by `(`, file paths (if `files`) and HTTP routes."""
    out = set()
    for tok in _BACKTICK_RE.findall(text or ""):
        m = re.match(r"[A-Za-z_$][\w$./\-]*", tok.strip())
        if m:
            out.add(m.group(0))
    out.update(_CALL_RE.findall(text or ""))
    if files:
        out.update(_PATH_RE.findall(text or ""))
    out.update(r.split(None, 1)[1] for r in _ROUTE_RE.findall(text or ""))
    return {s for s in out if len(s) >= 4 and s.lower() not in _STOP}


def _mentions(text: str, symbols: set) -> bool:
    return any(s in text for s in symbols)


def _has_interface(contract: str) -> bool:
    return bool(_CALL_RE.search(contract) is not None and
                any(s for s in _CALL_RE.findall(contract) if s.lower() not in _STOP)
                or _SHAPE_RE.search(contract) or _ROUTE_RE.search(contract)
                or _EXPORTS_LIST_RE.search(contract))


def _has_wire_interface(contract: str) -> bool:
    """HTTP route, message/object shape or an explicit messages/emits list."""
    return bool(_SHAPE_RE.search(contract) or _ROUTE_RE.search(contract)
                or re.search(r"\b(?:emits|messages)\s*:", contract, re.I))


_BARE_FILE_RE = re.compile(
    r"(?<![\w./\-])(?:Dockerfile|Makefile|LICENSE[\w.\-]*|README[\w.\-]*|"
    r"docker-compose[\w.\-]*|\.env[\w.\-]*|\.dockerignore)(?![\w\-])")
_OWNED_RE = re.compile(r"files? owned|owned files?", re.I)
_TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs"}
_TEST_FILE_RE = re.compile(r"\.test\.|\.spec\.|_test\.|^test_", re.I)
_INFRA_DIRS = {"deploy", ".github", ".gitlab", ".circleci"}
_INFRA_FILE_RE = re.compile(
    r"^(?:dockerfile|makefile|docker-compose[\w.\-]*|\.dockerignore|\.env[\w.\-]*"
    r"|[\w.\-]*\.service|\.gitlab-ci\.ya?ml|jenkinsfile)$", re.I)
_DOCS_FILE_RE = re.compile(r"^(?:readme|license|licence|changelog)[\w.\-]*$|\.md$", re.I)


def _owned_window(contract: str) -> str:
    i = _OWNED_RE.search(contract or "")
    return contract[i.start():i.start() + 300] if i else (contract or "")


def _file_kind(f: str) -> str:
    """Kind of one file path: tests | docs | infra | code | other."""
    parts = [x for x in f.replace("\\", "/").strip("/").split("/") if x]
    if not parts:
        return "other"
    base = parts[-1]
    if f.endswith("/"):
        base = ""
    dirs = [x.lower() for x in parts[:-1] + ([parts[-1]] if f.endswith("/") else [])]
    if any(d in _TEST_DIRS for d in dirs) or _TEST_FILE_RE.search(base):
        return "tests"
    if any(d in _INFRA_DIRS for d in dirs) or _INFRA_FILE_RE.match(base):
        return "infra"
    if "docs" in dirs or _DOCS_FILE_RE.search(base):
        return "docs"
    if _CODE_FILE_RE.search(base):
        return "code"
    return "other"


def _files_of(text: str) -> list:
    return _PATH_RE.findall(text or "") + _BARE_FILE_RE.findall(text or "")


def module_kind(module: dict) -> str:
    """"code" | "tests" | "docs" | "infra" | "unknown" from the module's path,
    name and the files-owned window of its contract."""
    name = str(module.get("name") or "")
    path = str(module.get("path") or "").strip()
    # A path that is itself tests/docs/infra is decisive (window text may
    # mention code files, e.g. a Dockerfile CMD).
    pk = _file_kind(path) if path else "other"
    if pk in ("tests", "docs", "infra"):
        return pk
    files = ([path] if path else []) + _files_of(_owned_window(str(module.get("contract") or "")))
    kinds = [_file_kind(f) for f in files]
    if "code" in kinds:
        return "code"
    if name.lower() in ("test", "tests") or "tests" in kinds:
        return "tests"
    known = [k for k in kinds if k != "other"]
    if known and all(k == "docs" for k in known):
        return "docs"
    if known and all(k in ("docs", "infra") for k in known):
        return "infra"
    return "unknown"


def _reach(tasks_by_id: dict, start: str) -> set:
    """Task ids reachable from `start` through depends_on (excluding start)."""
    seen, stack = set(), list(tasks_by_id[start].get("depends_on") or [])
    while stack:
        t = stack.pop()
        if t in seen or t not in tasks_by_id:
            continue
        seen.add(t)
        stack.extend(tasks_by_id[t].get("depends_on") or [])
    return seen


def heuristic_violations(plan: dict) -> list:
    """task_touches_deployment / process_acceptance_criteria as warnings."""
    out = []
    for t in plan.get("tasks") or []:
        title = t.get("title") or ""
        text = "%s\n%s" % (title, t.get("prompt") or "")
        if not _AUTHOR_RE.search(title) and _DEPLOY_RE.search(text):
            out.append(_vtn(
                "task_touches_deployment", "warn", t.get("id"),
                "task %s (%s) looks like a deployment/remote step; pipez "
                "deploys after integration" % (t.get("id"), title),
                "Remove the deployment/remote step from this task; pipez "
                "deploys after integration."))
        for a in t.get("acceptance") or []:
            if _PROCESS_RE.search(a):
                out.append(_vtn(
                    "process_acceptance_criteria", "warn", t.get("id"),
                    "task %s: %s" % (t.get("id"), a),
                    "Rewrite the criterion to check the product, not the "
                    "builder's report or process."))
    return out


def heuristic_warnings(plan: dict) -> list:
    """The heuristic violations as the legacy 'code: message' strings."""
    return ["%s: %s" % (v["code"], v["message"]) for v in heuristic_violations(plan)]


def check_plan(plan: dict) -> list:
    modules = [m for m in plan.get("modules") or [] if isinstance(m, dict)]
    tasks = [t for t in plan.get("tasks") or [] if isinstance(t, dict)]
    mods = {m.get("name"): m for m in modules}
    tasks_by_id = {t.get("id"): t for t in tasks}
    mod_tasks = {n: [t for t in tasks if t.get("module") == n] for n in mods}
    dependents = {n: [m["name"] for m in modules if n in (m.get("depends_on") or [])]
                  for n in mods}
    contracts = {n: str(m.get("contract") or "") for n, m in mods.items()}
    csyms = {n: _symbols(c) for n, c in contracts.items()}
    isyms = {n: _symbols(c, files=False) for n, c in contracts.items()}
    kinds = {n: module_kind(m) for n, m in mods.items()}
    v = []

    for m in modules:
        for d in m.get("depends_on") or []:
            if d not in mods:
                v.append(_vtn("module_dep_unknown", "error", m.get("name"),
                              "module %s depends on %r, which is not a module"
                              % (m.get("name"), d),
                              "Depend only on names listed in modules, or remove the edge."))

    for t in tasks:
        if t.get("module") not in mods:
            v.append(_vtn("task_module_unknown", "error", t.get("id"),
                          "task %s has module %r, which is not a module name"
                          % (t.get("id"), t.get("module")),
                          "Set module to the one module this task owns."))

    # A task may build against a dependency module without depending on its
    # tasks only if its prompt restates that module's interface.
    for t in tasks:
        m = mods.get(t.get("module"))
        if not m or t.get("id") not in tasks_by_id:
            continue
        if kinds[m["name"]] in ("docs", "infra"):
            continue
        reach = _reach(tasks_by_id, t["id"])
        for d in m.get("depends_on") or []:
            if d not in mods or not mod_tasks.get(d) or kinds[d] in _NONCODE:
                continue
            cand = [x["id"] for x in mod_tasks[d]]
            if reach.intersection(cand) or t["id"] in cand:
                continue
            if _mentions(str(t.get("prompt") or ""), isyms[d]):
                continue
            v.append(_vtn(
                "dep_coverage", "error", t["id"],
                "task %s (module %s) does not depend on any task of module %s "
                "(candidates: %s) nor name its interface"
                % (t["id"], m["name"], d, ", ".join(cand)),
                "Add one of %s to %s.depends_on so it is built against real "
                "%s code." % (", ".join(cand), t["id"], d)))

    for m in modules:
        n, c = m.get("name"), contracts.get(m.get("name"), "")
        if kinds[n] in _NONCODE:
            continue
        text = c + "\n" + "\n".join(str(t.get("prompt") or "") for t in mod_tasks.get(n, []))
        owned = _owned_window(c)
        network = bool(_CODE_FILE_RE.search(owned) and _NETWORK_RE.search(text))
        ok = _has_interface(c) and (not network or _has_wire_interface(c))
        if not ok:
            sev = "error" if dependents.get(n) or network else "warn"
            v.append(_vtn(
                "contract_interface", sev, n,
                "contract of %s does not enumerate its public interface%s"
                % (n, " (routes or message shapes)" if network else ""),
                "List every exported function signature%s that other modules "
                "or clients rely on." % (", HTTP route and message shape"
                                         if network else "")))
    for m in modules:
        n = m.get("name")
        if not _PATH_RE.search(contracts.get(n, "")):
            v.append(_vtn("contract_files", "warn", n,
                          "contract of %s names no owned file" % n,
                          "Name the files this module owns."))

    for m in modules:
        n = m.get("name")
        text = contracts[n] + "\n" + "\n".join(
            str(t.get("prompt") or "") for t in mod_tasks.get(n, []))
        if kinds[n] in ("docs", "infra"):
            continue
        for d in m.get("depends_on") or []:
            if d not in mods:
                continue
            if kinds[n] == "tests" and any(
                    _file_kind(f) == "tests" for f in _files_of(text)):
                continue
            if re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(d), text, re.I) \
                    or _mentions(text, csyms[d]):
                continue
            v.append(_vtn("consumer_names_interface", "error", n,
                          "%s depends on %s but neither its contract nor its tasks "
                          "mention %s or any symbol of its interface" % (n, d, d),
                          "Name the %s functions/messages that %s uses." % (d, n)))

    minc = config.PLAN_GATE_MIN_PROMPT_CHARS
    for t in tasks:
        prompt = str(t.get("prompt") or "")
        mn = t.get("module")
        kind = kinds.get(mn, "code")
        if kind in ("docs", "infra"):
            continue
        syms = set(csyms.get(mn, set()))
        if kind == "tests":
            for d in mods[mn].get("depends_on") or []:
                syms |= csyms.get(d, set())
        short = kind != "tests" and len(prompt) < minc
        nameless = bool(mn in mods and syms and not _mentions(prompt, syms))
        if not (short or nameless):
            continue
        why = []
        if short:
            why.append("prompt is %d characters (minimum %d)" % (len(prompt), minc))
        if nameless:
            why.append("prompt names no symbol or file from the %s contract%s" % (
                mn, " or its dependencies" if kind == "tests" else ""))
        sev = "error" if (short and nameless) or (kind == "tests" and nameless) else "warn"
        v.append(_vtn("task_prompt_thin", sev, t.get("id"),
                      "task %s: %s" % (t.get("id"), "; ".join(why)),
                      "Spell out the exact functions, files, messages and "
                      "behaviour this task implements, using the contract's names."))

    for t in tasks:
        acc = [str(a) for a in t.get("acceptance") or []]
        if not any(_BACKTICK_RE.search(a) or _HTTP_STATUS_RE.search(a)
                   or _ROUTE_RE.search(a) or _PATH_RE.search(a) for a in acc):
            v.append(_vtn("acceptance_not_checkable", "warn", t.get("id"),
                          "task %s has no acceptance item with a command, HTTP "
                          "status/path or file path" % t.get("id"),
                          "Add a criterion that names a command to run, an HTTP "
                          "status or a file to check."))

    for m in modules:
        n, c = m.get("name"), contracts.get(m.get("name"), "")
        if _MANUAL_RE.search(c):
            rest = "\n".join(ln for ln in re.split(r"(?<=[.!?])\s+|\n", c)
                             if not _MANUAL_RE.search(ln) and not _NOAUTO_RE.search(ln))
            if not _AUTOTEST_RE.search(rest):
                v.append(_vtn("manual_testing_only", "warn", n,
                              "the only testing stated for %s is manual" % n,
                              "State an automated test (file and command) for this module."))

    v.extend(heuristic_violations(plan))
    return v


def format_note(violations: list) -> str:
    lines = ["AUTOMATED PLAN GATE — the previous plan failed these checks; fix "
             "every ERROR, keep everything else unchanged"]
    for x in violations:
        lines.append("- [%s] %s (%s): %s — %s" % (
            "ERROR" if x["severity"] == "error" else "WARN",
            x["code"], x["subject"], x["message"], x["hint"]))
    return "\n".join(lines)
