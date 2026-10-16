#!/usr/bin/env python3
"""Replay the stored intake/brief prompts of past runs through several tiers
and report schema validity, agreement with the stored answers, latency and
estimated cost. The source DB is never modified (a temp copy is read).

  python scripts/bench_local.py --db pipez.sqlite3 --runs all \
      --stages intake,brief --tiers local,light,standard --out bench --yes
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

from pipez import config  # noqa: E402
from pipez.db import Database  # noqa: E402
from pipez.engine import Engine  # noqa: E402
from pipez.events import EventBus  # noqa: E402
from pipez.llm import LLM  # noqa: E402

DEFAULT_OUT_TOKENS = {"intake": 3000, "brief": 2500}


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", required=True, help="path to the pipez sqlite DB (read only)")
    p.add_argument("--runs", default="all", help="comma list of run ids, or 'all'")
    p.add_argument("--stages", default="intake,brief")
    p.add_argument("--tiers", default="local,light,standard")
    p.add_argument("--out", default="bench", help="output directory")
    p.add_argument("--max-per-stage", type=int, default=2,
                   help="max runs replayed per stage (bounds spend)")
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
        if stage == "brief" and not (r.get("decisions") and r.get("answers")):
            continue
        if stage == "intake" and not r.get("decisions"):
            continue
        rows.append(r)
    return rows[:limit]


def build_prompt(engine, run: dict, stage: str) -> tuple:
    if stage == "intake":
        return engine._intake_prompt(run)
    return engine._brief_prompt(run)


def estimate(tiers, prompt_chars, stage) -> float:
    total = 0.0
    for t in tiers:
        tier = config.MODEL_TIERS.get(t)
        if tier:
            total += config.estimate_cost(tier["model"], prompt_chars // 4,
                                          DEFAULT_OUT_TOKENS[stage])
    return total


def agreement_intake(out: dict, run: dict) -> dict:
    new = {d["key"]: d.get("proposed_default") for d in out.get("decisions") or []}
    old = {d["key"]: d.get("proposed_default") for d in run.get("decisions") or []
           if not d.get("free_text")}
    union, both = set(new) | set(old), set(new) & set(old)
    return {"key_jaccard": len(both) / len(union) if union else 1.0,
            "default_agree": (sum(1 for k in both if new[k] == old[k]) / len(both))
            if both else None}


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


async def one_call(llm, stage, tier_name, system, prompt, run) -> dict:
    tier = config.MODEL_TIERS[tier_name]
    rec = {"run_id": run["id"], "stage": stage, "tier": tier_name,
           "model": tier["model"]}
    started = time.time()
    try:
        fn = llm.intake if stage == "intake" else llm.brief
        out, usage = await fn(system, prompt, tier)
        rec.update(ok=True, schema_valid=True, seconds=round(time.time() - started, 2),
                   input_tokens=usage.get("input_tokens", 0),
                   output_tokens=usage.get("output_tokens", 0))
        rec["cost"] = config.estimate_cost(usage.get("model") or tier["model"],
                                           rec["input_tokens"], rec["output_tokens"])
        rec.update(agreement_intake(out, run) if stage == "intake"
                   else agreement_brief(out, run))
    except Exception as e:  # provider validates the schema; record any failure
        rec.update(ok=False, schema_valid=False, error=str(e)[:500],
                   seconds=round(time.time() - started, 2), cost=0.0)
    return rec


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.mean(xs) if xs else None


def _fmt(x, f="%.2f"):
    return "-" if x is None else f % x


def table(results) -> str:
    lines = ["| stage | tier | n | ok% | p50 s | mean $ | key-Jaccard | default-agree% | len ratio | headings% |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for stage in sorted({r["stage"] for r in results}):
        for tier in dict.fromkeys(r["tier"] for r in results if r["stage"] == stage):
            rs = [r for r in results if r["stage"] == stage and r["tier"] == tier]
            oks = [r for r in rs if r["ok"]]
            da = _mean([r.get("default_agree") for r in oks])
            hs = [r["headings_present"] / r["headings_stored"] for r in oks
                  if r.get("headings_stored")]
            lines.append("| %s | %s | %d | %s | %s | %s | %s | %s | %s | %s |" % (
                stage, tier, len(rs), _fmt(100 * len(oks) / len(rs), "%.0f"),
                _fmt(statistics.median([r["seconds"] for r in rs]) if rs else None, "%.1f"),
                _fmt(_mean([r["cost"] for r in rs]), "%.4f"),
                _fmt(_mean([r.get("key_jaccard") for r in oks])),
                _fmt(None if da is None else 100 * da, "%.0f"),
                _fmt(_mean([r.get("length_ratio") for r in oks])),
                _fmt(None if not hs else 100 * _mean(hs), "%.0f")))
    return "\n".join(lines)


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
    unknown = [t for t in tiers if t not in config.MODEL_TIERS]
    if unknown:
        print("tiers not configured: %s (local needs PIPEZ_LOCAL_BASE_URL/MODEL)"
              % ", ".join(unknown), file=sys.stderr)
        return 2
    if not args.dry_run:
        need = []
        if any(config.MODEL_TIERS[t]["provider"] == "anthropic" for t in tiers) \
                and not config.ANTHROPIC_API_KEY:
            need.append("ANTHROPIC_API_KEY")
        if "local" in tiers and not (config.LOCAL_BASE_URL and config.LOCAL_MODEL):
            need.append("PIPEZ_LOCAL_BASE_URL/PIPEZ_LOCAL_MODEL")
        if need:
            print("missing in env: %s" % ", ".join(need), file=sys.stderr)
            return 2

    wanted = None if args.runs == "all" else set(args.runs.split(","))
    tmp = tempfile.mkdtemp(prefix="pipez-bench-")
    db = Database(copy_db(args.db, tmp))
    engine = Engine(db, EventBus(), LLM())     # built outside a loop: no local probe

    jobs, spend = [], 0.0
    for stage in stages:
        for run in select_runs(db, stage, wanted, args.max_per_stage):
            system, prompt = build_prompt(engine, run, stage)
            est = estimate(tiers, len(system) + len(prompt), stage)
            spend += est
            jobs.append((stage, run, system, prompt))
            print("plan: %-6s run %s  prompt %d chars  tiers %s  est $%.4f" % (
                stage, run["id"], len(system) + len(prompt), ",".join(tiers), est))
    print("calls: %d   estimated spend: $%.4f (local is free but ~9 tok/s)"
          % (len(jobs) * len(tiers), spend))
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
            for t in tiers:
                rec = await one_call(llm, stage, t, system, prompt, run)
                print("  %s %s %s ok=%s %.1fs" % (stage, run["id"], t, rec["ok"], rec["seconds"]))
                res.append(rec)
        return res

    results = asyncio.run(run_all())
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "bench-%s.json" % time.strftime("%Y%m%d-%H%M%S"))
    with open(path, "w") as f:
        json.dump({"db": args.db, "tiers": tiers, "stages": stages,
                   "estimated_spend": spend, "results": results}, f, indent=2)
    print()
    print(table(results))
    print("\nwrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
