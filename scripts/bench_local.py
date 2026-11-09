#!/usr/bin/env python3
"""Replay the stored intake/brief prompts of past runs through several tiers
and report schema validity, agreement with the stored answers, latency and
estimated cost. The source DB is never modified (a temp copy is read).

  python scripts/bench_local.py --db pipez.sqlite3 --runs all \
      --stages intake,brief --tiers local,light,standard --out bench --yes

New request texts can be benchmarked too (intake only), scoring every tier
against a reference tier called in the same run:

  python scripts/bench_local.py --db pipez.sqlite3 --stages intake \
      --tiers deep,standard --reference-tier deep \
      --request-files .odin/bench/requests --max-per-stage 0 --yes

Plan stages (first plans and revision rounds) take tier@effort settings:

  python scripts/bench_local.py --db pipez.sqlite3 --stages plan,plan_revision \
      --tiers deep@high,deep@medium,standard --save-outputs --yes
"""
import argparse
import asyncio
import json
import os
import re
import sqlite3
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from pipez import config, plangate  # noqa: E402
from pipez.db import Database  # noqa: E402
from pipez.engine import Engine  # noqa: E402
from pipez.events import EventBus  # noqa: E402
from pipez.llm import LLM  # noqa: E402

DEFAULT_OUT_TOKENS = {"intake": 3000, "brief": 2500, "plan": 20000,
                      "plan_revision": 15000}
PLAN_STAGES = ("plan", "plan_revision")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", required=True, help="path to the pipez sqlite DB (read only)")
    p.add_argument("--runs", default="all", help="comma list of run ids, or 'all'")
    p.add_argument("--stages", default="intake,brief")
    p.add_argument("--tiers", default="local,light,standard",
                   help="comma list of name[@effort], e.g. deep@high,deep@medium,standard")
    p.add_argument("--out", default="bench", help="output directory")
    p.add_argument("--max-per-stage", type=int, default=2,
                   help="max runs replayed per stage (bounds spend)")
    p.add_argument("--request-files", default="",
                   help="comma list of .md request files, or a directory of them "
                        "(synthetic intake jobs; not limited by --max-per-stage)")
    p.add_argument("--project-id", default="bench",
                   help="project id used for synthetic requests (known keys)")
    p.add_argument("--reference-tier", default="",
                   help="intake/plan stages: call this tier (name[@effort]) first and "
                        "score the other tiers against its output instead of "
                        "the stored answer")
    p.add_argument("--gate-rounds", type=int, default=0,
                   help="plan stages: when the plan has gate errors, send up to N "
                        "gate-note revision calls on the same tier (0 = measure only)")
    p.add_argument("--save-outputs", action="store_true",
                   help="store the raw output of every call in the JSON file")
    p.add_argument("--yes", action="store_true", help="proceed after the spend estimate")
    p.add_argument("--dry-run", action="store_true",
                   help="print the plan and estimate; make no calls")
    return p.parse_args(argv)


def copy_db(src: str, dest_dir: str) -> str:
    """Consistent copy of the source DB (read-only open of the original)."""
    dest = os.path.join(dest_dir, "bench-copy.sqlite3")
    ro = sqlite3.connect("file:%s?mode=ro" % os.path.abspath(src), uri=True)
    try:
        out = sqlite3.connect(dest)
        ro.backup(out)
        out.close()
    finally:
        ro.close()
    return dest


def select_runs(db, stage: str, wanted, limit: int) -> list:
    rows = []
    for r in db.get_runs(limit=100000):
        if wanted is not None and r["id"] not in wanted:
            continue
        if not r.get("request"):
            continue
        if stage == "plan" and not (r.get("plan") and r.get("brief")):
            continue
        if stage == "plan_revision" and not (
                r.get("plan") and r.get("brief")
                and (r.get("plan_meta") or {}).get("revisions")):
            continue
        if stage == "brief" and not (r.get("decisions") and r.get("answers")):
            continue
        if stage == "intake" and not r.get("decisions"):
            continue
        rows.append(r)
    return rows[:limit]


def synthetic_runs(spec: str, project_id: str) -> list:
    """Run dicts for each request file (or every *.md in a directory)."""
    paths = []
    for item in [x.strip() for x in spec.split(",") if x.strip()]:
        if os.path.isdir(item):
            paths += sorted(os.path.join(item, f) for f in os.listdir(item)
                            if f.endswith(".md"))
        else:
            paths.append(item)
    runs = []
    for path in paths:
        with open(path) as f:
            text = f.read()
        base = os.path.splitext(os.path.basename(path))[0]
        runs.append({"id": "req:%s" % base, "request": text, "context": "",
                     "project_id": project_id, "decisions": None,
                     "answers": None, "brief": None, "request_file": path})
    return runs


def parse_tier(spec: str) -> tuple:
    """'name[@effort]' -> (name, effort or None)."""
    name, _, effort = spec.partition("@")
    return name, (effort or None)


def tier_dict(spec: str) -> dict:
    name, effort = parse_tier(spec)
    tier = config.MODEL_TIERS[name]
    return dict(tier, effort=effort) if effort else tier


def build_prompt(engine, run: dict, stage: str) -> tuple:
    if stage == "intake":
        return engine._intake_prompt(run)
    if stage == "plan":
        return engine._plan_prompt(dict(run, plan=None))    # no revision branch
    if stage == "plan_revision":
        return engine._plan_prompt(run)
    return engine._brief_prompt(run)


def estimate(tiers, prompt_chars, stage) -> float:
    total = 0.0
    for t in tiers:
        tier = config.MODEL_TIERS.get(parse_tier(t)[0])
        if tier:
            total += config.estimate_cost(tier["model"], prompt_chars // 4,
                                          DEFAULT_OUT_TOKENS[stage])
    return total


def _gate_counts(violations) -> tuple:
    errs = sum(1 for v in violations if v["severity"] == "error")
    return errs, len(violations) - errs


def _clean(engine, out):
    return engine._validate_plan("bench", out)


async def _gate_loop(llm, engine, rec, stage, tier, tier_name, run, plan,
                     violations, rounds, save=False):
    """Gate-note revision rounds on the same tier; updates `rec` in place and
    returns the final plan."""
    errs = _gate_counts(violations)[0]
    rec["gate_round_errors"] = [errs]
    rec["gate_rounds_used"] = 0
    rec["gate_passed"] = errs == 0
    base = dict(run, plan=None) if stage == "plan" else run
    fn = llm.plan
    for _ in range(rounds):
        if errs == 0:
            break
        system, prompt = engine._plan_prompt(
            base, gate_plan=plan, gate_note=plangate.format_note(violations))
        t0 = time.time()
        try:
            out, usage = await fn(system, prompt, tier)
            plan = _clean(engine, out)
        except Exception as e:
            rec["seconds"] = round(rec["seconds"] + time.time() - t0, 2)
            rec["gate_passed"] = False
            rec["gate_round_error"] = str(e)[:500]
            return plan
        rec["seconds"] = round(rec["seconds"] + time.time() - t0, 2)
        rec["input_tokens"] += usage.get("input_tokens", 0)
        rec["output_tokens"] += usage.get("output_tokens", 0)
        rec["cost"] += config.estimate_cost(
            usage.get("model") or tier["model"],
            usage.get("input_tokens", 0), usage.get("output_tokens", 0))
        if save:
            rec["final_output"] = out
            rec["round_outputs"].append(out)
        violations = plangate.check_plan(plan)
        errs, warns = _gate_counts(violations)
        rec["gate_rounds_used"] += 1
        rec["gate_round_errors"].append(errs)
        rec["gate_errors"], rec["gate_warnings"] = errs, warns
        rec["gate_codes"] = sorted({v["code"] for v in violations
                                    if v["severity"] == "error"})
        if save:
            rec["gate_violations"] = violations
    rec["gate_passed"] = errs == 0
    return plan


def _jaccard(a: set, b: set) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 1.0


def agreement_intake(out: dict, run: dict, reference: dict = None) -> dict:
    """Compare an intake output with the stored run decisions, or with
    `reference` (another intake output) when given."""
    new = {d["key"]: d.get("proposed_default") for d in out.get("decisions") or []}
    if reference is not None:
        ref_decisions = reference.get("decisions") or []
        locked_ref = set(reference.get("request_values") or {})
    else:
        ref_decisions = run.get("decisions") or []
        locked_ref = {d["key"] for d in ref_decisions if d.get("locked")}
    old = {d["key"]: d.get("proposed_default") for d in ref_decisions
           if not d.get("free_text")}
    union, both = set(new) | set(old), set(new) & set(old)
    return {"key_jaccard": len(both) / len(union) if union else 1.0,
            "default_agree": (sum(1 for k in both if new[k] == old[k]) / len(both))
            if both else None,
            "locked_agree": _jaccard(set(out.get("request_values") or {}), locked_ref)}


_SCAFFOLD_FORBIDDEN = re.compile(r"\bgit\s+(init|checkout|clone)\b|\bnpm\s+init\b")


def agreement_plan(out: dict, run: dict, reference: dict = None) -> dict:
    """Structure metrics of a plan output, compared with the stored plan or
    with `reference` (another plan output) when given."""
    base = reference if reference is not None else (run.get("plan") or {})
    tasks = out.get("tasks") or []

    def target(p):
        return (p.get("deploy") or {}).get("target") or "none"

    forbidden = bool(run.get("parent_run_id")) and any(
        _SCAFFOLD_FORBIDDEN.search(str(c)) for c in out.get("scaffold_commands") or [])
    return {"tasks": len(tasks), "modules": len(out.get("modules") or []),
            "deploy_target": target(out),
            "deploy_target_match": target(out) == target(base),
            "task_count_delta": len(tasks) - len(base.get("tasks") or []),
            "scaffold_forbidden": forbidden,
            "has_test_command": bool((out.get("test_command") or "").strip()),
            "plan_chars": len(json.dumps(out))}


def _brief_text(out: dict) -> str:
    text = (out.get("brief") or "").rstrip()
    if out.get("constraints"):
        text += "\n\n## Constraints\n" + "\n".join("- %s" % c for c in out["constraints"])
    return text


def agreement_brief(out: dict, run: dict) -> dict:
    stored = re.split(r"\n## Decisions\b", run.get("brief") or "")[0]
    new = _brief_text(out)
    heads = re.findall(r"^## .+$", stored, re.M)
    present = sum(1 for h in heads if h.strip() in new)
    return {"length_ratio": len(new) / len(stored) if stored else None,
            "headings_present": present, "headings_stored": len(heads)}


async def one_call(llm, stage, tier_name, system, prompt, run,
                   reference=None, is_reference=False, reference_tier=None,
                   capture=None, save_output=False, engine=None, gate_rounds=0) -> dict:
    """One tier call. `reference` is the reference tier's output (None when it
    failed or is unused); `capture["out"]` receives this call's raw output."""
    tier = tier_dict(tier_name)
    rec = {"run_id": run["id"], "stage": stage, "tier": tier_name,
           "model": tier["model"]}
    if run.get("request_file"):
        rec["request_file"] = run["request_file"]
    if reference_tier:
        rec["reference_tier"] = reference_tier
    started = time.time()
    try:
        fn = {"intake": llm.intake, "brief": llm.brief}.get(stage, llm.plan)
        out, usage = await fn(system, prompt, tier)
        rec.update(ok=True, schema_valid=True, seconds=round(time.time() - started, 2),
                   input_tokens=usage.get("input_tokens", 0),
                   output_tokens=usage.get("output_tokens", 0))
        if usage.get("structured"):
            rec["structured"] = usage["structured"]
        rec["cost"] = config.estimate_cost(usage.get("model") or tier["model"],
                                           rec["input_tokens"], rec["output_tokens"])
        if capture is not None:
            capture["out"] = out
        if stage in PLAN_STAGES:
            if is_reference:
                rec["agreement"] = "reference"
                rec.update(agreement_plan(out, run, {}))
                rec["deploy_target_match"] = None
                rec["task_count_delta"] = None
            else:
                rec.update(agreement_plan(out, run, reference))
            rec["output"] = out
            if engine is not None and not is_reference:
                await _plan_gate(llm, engine, rec, stage, tier_name, tier, run,
                                 out, gate_rounds, save_output)
        elif stage == "intake" and is_reference:
            rec["agreement"] = "reference"
        elif stage == "intake" and reference_tier:
            if reference is not None:
                rec.update(agreement_intake(out, run, reference))
            else:
                rec.update(key_jaccard=None, default_agree=None, locked_agree=None)
        elif stage == "intake":
            if run.get("decisions") is not None:
                rec.update(agreement_intake(out, run))
        else:
            rec.update(agreement_brief(out, run))
        if save_output and "output" not in rec:
            rec["output"] = out
    except Exception as e:  # provider validates the schema; record any failure
        rec.update(ok=False, schema_valid=False, error=str(e)[:500],
                   seconds=round(time.time() - started, 2), cost=0.0)
    return rec


async def _plan_gate(llm, engine, rec, stage, tier_name, tier, run, out, rounds, save=False):
    if save:
        rec["final_output"] = out
        rec["round_outputs"] = [out]
    ref_plan = run.get("plan")
    if ref_plan:
        re_, rw = _gate_counts(plangate.check_plan(ref_plan))
        rec["ref_gate_errors"], rec["ref_gate_warnings"] = re_, rw
    try:
        plan = _clean(engine, out)
    except Exception as e:
        rec["gate_error"] = "plan not cleanable: %s" % str(e)[:300]
        return
    violations = plangate.check_plan(plan)
    errs, warns = _gate_counts(violations)
    rec["gate_errors"], rec["gate_warnings"] = errs, warns
    rec["gate_codes"] = sorted({v["code"] for v in violations
                                if v["severity"] == "error"})
    if save:
        rec["gate_violations"] = violations
    if rounds > 0:
        await _gate_loop(llm, engine, rec, stage, tier, tier_name, run, plan,
                         violations, rounds, save)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.mean(xs) if xs else None


def _fmt(x, f="%.2f"):
    return "-" if x is None else f % x


def plan_table(results) -> str:
    rounds_on = any("gate_rounds_used" in r for r in results)
    gate_cols = " pass% | rounds |" if rounds_on else ""
    gate_sep = "---|---|" if rounds_on else ""
    lines = ["| stage | tier | n | ok% | p50 s | mean $ | mean out-tok | tasks | Δtasks | deploy-match% | forbidden% | gate-err | ref-err |" + gate_cols,
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|" + gate_sep]
    for stage in PLAN_STAGES:
        for tier in dict.fromkeys(r["tier"] for r in results if r["stage"] == stage):
            rs = [r for r in results if r["stage"] == stage and r["tier"] == tier]
            oks = [r for r in rs if r["ok"]]
            dm = [1.0 if r["deploy_target_match"] else 0.0 for r in oks
                  if r.get("deploy_target_match") is not None]
            fb = [1.0 if r["scaffold_forbidden"] else 0.0 for r in oks]
            gated = [r for r in oks if "gate_passed" in r]
            passed = [r for r in gated if r["gate_passed"]]
            extra = ""
            if rounds_on:
                extra = " %s | %s |" % (
                    _fmt(100 * len(passed) / len(gated) if gated else None, "%.0f"),
                    _fmt(_mean([r["gate_rounds_used"] for r in passed]), "%.1f"))
            lines.append("| %s | %s | %d | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                stage, tier, len(rs), _fmt(100 * len(oks) / len(rs), "%.0f"),
                _fmt(statistics.median([r["seconds"] for r in rs]) if rs else None, "%.1f"),
                _fmt(_mean([r["cost"] for r in rs]), "%.4f"),
                _fmt(_mean([r.get("output_tokens") for r in oks]), "%.0f"),
                _fmt(_mean([r.get("tasks") for r in oks]), "%.1f"),
                _fmt(_mean([r.get("task_count_delta") for r in oks]), "%+.1f"),
                _fmt(None if not dm else 100 * _mean(dm), "%.0f"),
                _fmt(None if not fb else 100 * _mean(fb), "%.0f"),
                _fmt(_mean([r.get("gate_errors") for r in oks]), "%.1f"),
                _fmt(_mean([r.get("ref_gate_errors") for r in oks]), "%.1f")) + extra)
    return "\n".join(lines)


def table(results) -> str:
    plan_rs = [r for r in results if r["stage"] in PLAN_STAGES]
    if plan_rs:
        rest = [r for r in results if r["stage"] not in PLAN_STAGES]
        return plan_table(plan_rs) + ("\n\n" + table(rest) if rest else "")
    lines = ["| stage | tier | n | ok% | p50 s | mean $ | key-Jaccard | locked-J | default-agree% | len ratio | headings% |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for stage in sorted({r["stage"] for r in results}):
        for tier in dict.fromkeys(r["tier"] for r in results if r["stage"] == stage):
            rs = [r for r in results if r["stage"] == stage and r["tier"] == tier]
            oks = [r for r in rs if r["ok"]]
            da = _mean([r.get("default_agree") for r in oks])
            hs = [r["headings_present"] / r["headings_stored"] for r in oks
                  if r.get("headings_stored")]
            lines.append("| %s | %s | %d | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                stage, tier, len(rs), _fmt(100 * len(oks) / len(rs), "%.0f"),
                _fmt(statistics.median([r["seconds"] for r in rs]) if rs else None, "%.1f"),
                _fmt(_mean([r["cost"] for r in rs]), "%.4f"),
                _fmt(_mean([r.get("key_jaccard") for r in oks])),
                _fmt(_mean([r.get("locked_agree") for r in oks])),
                _fmt(None if da is None else 100 * da, "%.0f"),
                _fmt(_mean([r.get("length_ratio") for r in oks])),
                _fmt(None if not hs else 100 * _mean(hs), "%.0f")))
    return "\n".join(lines)


def tiers_for(tiers, ref, stage) -> list:
    """Tiers called for a job: for intake/plan stages with a reference tier,
    the reference comes first (added when not already listed)."""
    if stage not in ("intake",) + PLAN_STAGES or not ref:
        return list(tiers)
    return [ref] + [t for t in tiers if t != ref]


def main(argv=None) -> int:
    args = parse_args(argv)
    stages = [s for s in args.stages.split(",") if s]
    tiers = [t for t in args.tiers.split(",") if t]
    bad = [s for s in stages if s not in DEFAULT_OUT_TOKENS]
    if bad:
        print("unknown stages: %s" % ", ".join(bad), file=sys.stderr)
        return 2
    if not os.path.exists(args.db):
        print("no such db: %s" % args.db, file=sys.stderr)
        return 2
    if config.MOCK:
        print("refusing to benchmark with the mock LLM (PIPEZ_MOCK is set)", file=sys.stderr)
        return 2
    ref = args.reference_tier
    unknown = [t for t in tiers + ([ref] if ref else [])
               if parse_tier(t)[0] not in config.MODEL_TIERS]
    if unknown:
        print("tiers not configured: %s (local needs PIPEZ_LOCAL_BASE_URL/MODEL, local_reason PIPEZ_LOCAL_REASON_BASE_URL/MODEL)"
              % ", ".join(unknown), file=sys.stderr)
        return 2
    if not args.dry_run:
        need = []
        used = [parse_tier(t)[0] for t in tiers + ([ref] if ref else [])]
        if any(config.MODEL_TIERS[t]["provider"] == "anthropic" for t in used) \
                and not config.ANTHROPIC_API_KEY:
            need.append("ANTHROPIC_API_KEY")
        for lt, prefix, url, model in (
                ("local", "PIPEZ_LOCAL", config.LOCAL_BASE_URL, config.LOCAL_MODEL),
                ("local_reason", "PIPEZ_LOCAL_REASON", config.LOCAL_REASON_BASE_URL,
                 config.LOCAL_REASON_MODEL)):
            if lt in used and not (url and model):
                need.append("%s_BASE_URL/%s_MODEL" % (prefix, prefix))
        if need:
            print("missing in env: %s" % ", ".join(need), file=sys.stderr)
            return 2

    wanted = None if args.runs == "all" else set(args.runs.split(","))
    tmp = tempfile.mkdtemp(prefix="pipez-bench-")
    db = Database(copy_db(args.db, tmp))
    engine = Engine(db, EventBus(), LLM())     # built outside a loop: no local probe

    synthetic = synthetic_runs(args.request_files, args.project_id) \
        if args.request_files else []
    jobs, spend, calls = [], 0.0, 0
    for stage in stages:
        runs = select_runs(db, stage, wanted, args.max_per_stage)
        if stage == "intake":
            runs = runs + synthetic
        for run in runs:
            system, prompt = build_prompt(engine, run, stage)
            job_tiers = tiers_for(tiers, ref, stage)
            est = estimate(job_tiers, len(system) + len(prompt), stage)
            if stage in PLAN_STAGES:
                est *= 1 + max(args.gate_rounds, 0)
            spend += est
            calls += len(job_tiers)
            jobs.append((stage, run, system, prompt))
            print("plan: %-6s run %s  prompt %d chars  tiers %s  est $%.4f" % (
                stage, run["id"], len(system) + len(prompt), ",".join(job_tiers), est))
    print("calls: %d   estimated spend: $%.4f (local is free but ~9 tok/s)"
          % (calls, spend))
    if args.dry_run:
        return 0
    if not jobs:
        print("nothing to replay")
        return 0
    if not args.yes:
        print("re-run with --yes to proceed", file=sys.stderr)
        return 1

    async def run_all():
        llm = LLM()
        res = []
        for stage, run, system, prompt in jobs:
            use_ref = ref if stage in ("intake",) + PLAN_STAGES else ""
            reference_out = None
            for t in tiers_for(tiers, ref, stage):
                cap = {}
                is_ref = bool(use_ref) and t == use_ref
                rec = await one_call(llm, stage, t, system, prompt, run,
                                     reference=reference_out, is_reference=is_ref,
                                     reference_tier=use_ref or None, capture=cap,
                                     save_output=args.save_outputs, engine=engine,
                                     gate_rounds=args.gate_rounds)
                if is_ref:
                    reference_out = cap.get("out")
                print("  %s %s %s ok=%s %.1fs" % (stage, run["id"], t, rec["ok"], rec["seconds"]))
                res.append(rec)
        return res

    results = asyncio.run(run_all())
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "bench-%s.json" % time.strftime("%Y%m%d-%H%M%S"))
    with open(path, "w") as f:
        json.dump({"db": args.db, "tiers": tiers, "reference_tier": ref or None, "stages": stages,
                   "estimated_spend": spend, "results": results}, f, indent=2)
    print()
    print(table(results))
    print("\nwrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
