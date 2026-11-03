"""Scoring command line. Run from the repo root:

    uv run python -m eval.scoring judge   --dataset martian --judge deepseek-flash --split dev
    uv run python -m eval.scoring report  --dataset martian --judge deepseek-flash --split dev
    uv run python -m eval.scoring members --dataset martian --judge deepseek-flash --split dev
    uv run python -m eval.scoring subsets --dataset martian --judge deepseek-flash --split dev

See eval/scoring/README.md.
"""

import argparse
import asyncio
import datetime as dt
import json
import os
import sys
from pathlib import Path
from collections import Counter

from tqdm import tqdm

from .board import label_stats, member_stats, select_default
from .gates import GATE1B_TOOLS
from .data import ROOT, iter_runs, load_dataset
from .llm import EndpointUnavailable, JudgeModel, LLMError
from .metrics import bootstrap, category_recall, per_pr, summarize
from .pipeline import RESULTS, judge_run, judge_slug, load_records
from .views import panel, parse_view, score

TEST_LOG = ROOT / "eval" / "reports" / "test-split-log.md"
DEFAULT_VIEWS = ["code-review", "board:full:cp", "board:default:cp", "board:full:confirmed", "board:full:raw"]
DEFAULT_COMPARE = ["board:full:cp,code-review", "board:default:cp,code-review"]


def pr_ids(args) -> list[str]:
    """PR ids of the split. Touching test PRs needs --release and is logged (PLAN 6.6)."""
    ds = load_dataset(args.dataset)
    if args.split != "dev" and ds.split.get("test"):
        if not args.release:
            sys.exit("Scoring test PRs needs --release '<why>' (PLAN 6.6). Use --split dev otherwise.")
        TEST_LOG.parent.mkdir(parents=True, exist_ok=True)
        new = not TEST_LOG.exists()
        with open(TEST_LOG, "a", encoding="utf-8") as f:
            if new:
                f.write("# Test split access log\n\n| When | Dataset | Split | Command | Why |\n|---|---|---|---|---|\n")
            f.write(f"| {dt.datetime.now():%Y-%m-%d %H:%M} | {args.dataset} | {args.split} | {args.cmd} | {args.release} |\n")
    return ds.pr_ids(args.split)


async def cmd_judge(args) -> None:
    ds = load_dataset(args.dataset)
    ids = pr_ids(args)
    runs = iter_runs(args.dataset, args.variants.split(","), ids)
    print(f"{len(runs)} finished runs on {len(ids)} PRs")
    for model in args.judge.split(","):
        jm = JudgeModel(model, concurrency=args.concurrency, rate=args.rate, max_wait=args.max_wait * 60,
                        log=tqdm.write)
        try:
            served = await jm.identify()
        except (LLMError, EndpointUnavailable) as e:
            print(f"{model}: can't use the endpoint: {e}")
            return
        print(f"judge {model}: fingerprint {jm.fingerprint}, served {json.dumps(served)}")
        log = RESULTS / args.dataset / "judged" / judge_slug(model) / "sessions.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": dt.datetime.now().isoformat(timespec="seconds"),
                                "fingerprint": jm.fingerprint, "served": served}) + "\n")
        failed = []
        for run in tqdm(runs, desc=model, unit="run"):
            modes = [False, True] if args.whole else [False]
            for whole in modes:
                try:
                    await judge_run(run, ds.golden[run.pr_id], jm, whole=whole, force=args.force)
                except (LLMError, ValueError, KeyError) as e:
                    failed.append(f"{run.key}{' (whole)' if whole else ''}: {e}")
                except EndpointUnavailable as e:
                    print(f"STOPPED: {e}")
                    print(f"{model}: {jm.calls} calls this session. Finished calls are cached; re-run to continue.")
                    return
        print(f"{model}: {jm.calls} new calls, {len(failed)} failed runs")
        for line in failed:
            print("  " + line)
        if failed:
            print("Re-run the same command to retry; finished calls are cached.")


def _results(args, view, ids) -> dict:
    """{(pr_id, repeat): result} for a view, combining judges into a panel when several."""
    per_judge = []
    for model in args.judge.split(","):
        records = load_records(args.dataset, model, view.variant, ids)
        prints = Counter(r["judge"]["fingerprint"] for r in records)
        if len(prints) > 1:
            print(f"WARNING: {model} records for {view.variant} mix served versions {dict(prints)}; re-judge with --force")
        fallbacks = sum(r["dedup_fallback"] for r in records)
        if fallbacks:
            print(f"WARNING: {fallbacks} {view.variant} records used Martian's dedup fallback (no dedup)")
        per_judge.append({(r["pr_id"], r["repeat"]): score(r, view) for r in records})
    if len(per_judge) == 1:
        return per_judge[0]
    shared = set.intersection(*(set(j) for j in per_judge))
    return {k: panel([j[k] for j in per_judge]) for k in shared}


def _usage(args, variant, ids) -> str:
    runs = iter_runs(args.dataset, [variant.split("@")[0]], ids)
    costs = [r.meta["total_cost_usd"] for r in runs if "total_cost_usd" in r.meta]
    mins = [r.meta["duration_ms"] / 60000 for r in runs if "duration_ms" in r.meta]
    cost = f"${sum(costs) / len(costs):.2f}" if costs else "n/a"
    time = f"{sum(mins) / len(mins):.1f}" if mins else "n/a"
    return f"{cost} | {time}"


def cmd_report(args) -> None:
    ids = pr_ids(args)
    views = [parse_view(v) for v in (args.views or DEFAULT_VIEWS)]
    units, results = {}, {}
    for view in views:
        results[view.name] = _results(args, view, ids)
        units[view.name] = per_pr(results[view.name], args.profile)

    print(f"\n## {args.dataset}, split {args.split}, judge {args.judge}, profile {args.profile}\n")
    print("| View | PRs | Runs | Precision | Recall | F1 (± SE) | F2 | TP | FP | FN | Cost/run | Min/run |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for view in views:
        u = units[view.name]
        if not u:
            print(f"| {view.name} | 0 | 0 | | | | | | | | | |")
            continue
        s = summarize(u)
        se = bootstrap(u, n=args.bootstrap)["se"]
        print(f"| {view.name} | {s['prs']} | {len(results[view.name])} | {s['precision']:.1%} | {s['recall']:.1%} "
              f"| {s['f1']:.1%} ± {se:.1%} | {s['f2']:.1%} | {s['tp']:g} | {s['fp']:g} | {s['fn']:g} "
              f"| {_usage(args, view.variant, ids)} |")

    pairs = [p.split(",") for p in (args.compare or DEFAULT_COMPARE)]
    pairs = [(a, b) for a, b in pairs if units.get(a) and units.get(b)]
    if pairs:
        print("\n**Paired bootstrap over PRs** (difference A − B)\n")
        print("| A | B | PRs | ΔF1 | 95% CI | p | ΔRecall | ΔPrecision |")
        print("|---|---|---|---|---|---|---|---|")
        for a, b in pairs:
            f1 = bootstrap(units[a], units[b], "f1", n=args.bootstrap)
            rec = bootstrap(units[a], units[b], "recall", n=args.bootstrap)
            pre = bootstrap(units[a], units[b], "precision", n=args.bootstrap)
            lo, hi = f1["ci95"]
            print(f"| {a} | {b} | {f1['prs']} | {f1['point']:+.1%} | {lo:+.1%} to {hi:+.1%} | {'n/a' if f1['p_two_sided'] is None else format(f1['p_two_sided'], '.3f')} "
                  f"| {rec['point']:+.1%} | {pre['point']:+.1%} |")

    if args.categories:
        print("\n**Recall per golden category** (found / total, all categories)\n")
        cats = sorted({c for v in views for c in category_recall(results[v.name])}, key=str)
        print("| View | " + " | ".join(str(c) for c in cats) + " |")
        print("|---|" + "---|" * len(cats))
        for view in views:
            cr = category_recall(results[view.name])
            cells = [f"{cr[c][0]:g}/{cr[c][1]:g}" if c in cr else "" for c in cats]
            print(f"| {view.name} | " + " | ".join(cells) + " |")


def cmd_members(args) -> None:
    ids = pr_ids(args)
    records = load_records(args.dataset, args.judge, "board-full", ids)
    print(f"\n## Members: {args.dataset}, split {args.split}, judge {args.judge}, profile {args.profile}, "
          f"labels {args.labels}, {len(records)} runs\n")
    print("| Member | Golden found | Found by no other member | Candidates | Unmatched candidates | Precision |")
    print("|---|---|---|---|---|---|")
    for s in member_stats(records, args.profile, args.labels):
        print(f"| {s['member']} | {s['found']} | {s['unique']} | {s['candidates']} | {s['unmatched']} | {s['precision']:.1%} |")
    ls = label_stats(records, args.profile)
    print("\n**Labels** (a finding matches when any of its candidates matches a golden comment)\n")
    print("| Label | Findings | Matching a golden comment | Precision |")
    print("|---|---|---|---|")
    for label, s in ls["labels"].items():
        print(f"| {label} | {s['findings']} | {s['matched']} | {s['precision']:.1%} |")
    print(f"\nWrongful refutation rate: {ls['wrongful_refutation_rate']:.1%}. "
          f"Golden comments found before adjudication but lost by it: {ls['golden_lost_by_adjudication']}.")


def cmd_subsets(args) -> None:
    ids = pr_ids(args)
    records = load_records(args.dataset, args.judge, "board-full", ids)
    baseline = per_pr(_results(args, parse_view("code-review"), ids), args.profile)
    out = select_default(records, baseline, args.profile, args.labels, args.bootstrap)
    print(f"\n## Subsets: {args.dataset}, split {args.split}, judge {args.judge}, profile {args.profile}, labels {args.labels}\n")
    print(f"Best F1 {out['best']['f1']:.1%} (bootstrap SE {out['se']:.1%}); precision floor from /code-review "
          f"{out['precision_floor']:.1%}.\n")
    print("| Members | Size | Precision | Recall | F1 |")
    print("|---|---|---|---|---|")
    for r in out["rows"][: args.top]:
        print(f"| {','.join(r['members'])} | {len(r['members'])} | {r['precision']:.1%} | {r['recall']:.1%} | {r['f1']:.1%} |")
    c = out["chosen"]
    print("\nChosen default board: " + (f"{','.join(c['members'])} (F1 {c['f1']:.1%}, precision {c['precision']:.1%})"
                                         if c else "none meets the precision floor"))


async def cmd_gate1b(args) -> None:
    from .gates import gate1b_report, judge_stored, repeat_check

    martian = Path(args.martian_dir or os.environ.get("MARTIAN_DIR", ""))
    if not (martian / "results" / "benchmark_data.json").exists():
        sys.exit("Point --martian-dir (or MARTIAN_DIR) at the pinned Martian clone's offline/ folder.")
    tools = args.tools.split(",")
    report = ROOT / "eval" / "reports" / f"gate1b-{judge_slug(args.judge)}.md"
    if not args.report_only:
        jm = JudgeModel(args.judge, concurrency=args.concurrency, rate=args.rate, max_wait=args.max_wait * 60,
                        log=tqdm.write)
        try:
            served = await jm.identify()
            print(f"{dt.datetime.now():%H:%M} judge {args.judge}: fingerprint {jm.fingerprint}, served {json.dumps(served)}")
            await judge_stored(jm, martian, tools)
            repeat = await repeat_check(jm, args.repeat) if args.repeat else None
        except EndpointUnavailable as e:
            print(f"STOPPED: {e}")
            print(f"{jm.calls} calls this session. Finished calls are cached; re-run to continue.")
            return
        print(f"{dt.datetime.now():%H:%M} done: {jm.calls} calls this session")
    else:
        repeat = None
    text, passed = gate1b_report(args.judge, martian, tools, args.profile)
    if repeat:
        text += (f"\n\nRepeat check: {repeat['sampled']} cached judge prompts asked again; same verdict "
                 f"{repeat['same']}, changed {repeat['changed']}, failed {repeat['failed']} "
                 f"(agreement {repeat['agreement']:.1%}).")
    print(text)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(text + "\n", encoding="utf-8")
    print(f"\nWritten to {report.relative_to(ROOT)}")


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m eval.scoring")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("judge", "report", "members", "subsets"):
        s = sub.add_parser(name)
        s.add_argument("--dataset", required=True)
        s.add_argument("--judge", required=True, help="judge model(s); several, comma-separated, form a panel in report")
        s.add_argument("--split", choices=["dev", "test", "all"], default="dev")
        s.add_argument("--release", help="why test PRs are being scored; required for test or all (PLAN 6.6)")
        s.add_argument("--profile", choices=["strict", "core", "all"], default="core")
        s.add_argument("--bootstrap", type=int, default=2000, help="bootstrap resamples")
    j = sub.choices["judge"]
    j.add_argument("--variants", default="code-review,board-full")
    j.add_argument("--whole", action="store_true", help="also judge each run with all findings extracted as one comment")
    j.add_argument("--concurrency", type=int, default=1, help="calls in flight at once")
    j.add_argument("--rate", type=float, default=10.0, help="most calls started per minute")
    j.add_argument("--max-wait", type=float, default=180, help="minutes to wait out a busy or unreachable endpoint before stopping")
    j.add_argument("--force", action="store_true", help="re-judge records that already exist")
    r = sub.choices["report"]
    r.add_argument("--views", nargs="+", help="code-review | code-review@whole | board-full@whole | board:<full|default|id,id>:<confirmed|cp|raw>")
    r.add_argument("--compare", nargs="+", help="pairs A,B for the paired bootstrap")
    r.add_argument("--categories", action="store_true", help="recall per golden category")
    for name in ("members", "subsets"):
        sub.choices[name].add_argument("--labels", choices=["confirmed", "cp", "raw"], default="cp")
    sub.choices["subsets"].add_argument("--top", type=int, default=15)
    g = sub.add_parser("gate1b", help="local-judge agreement with the published judges (PLAN Phase 3)")
    g.add_argument("--judge", required=True)
    g.add_argument("--martian-dir", help="the pinned Martian clone's offline/ folder (or set MARTIAN_DIR)")
    g.add_argument("--tools", default=",".join(GATE1B_TOOLS))
    g.add_argument("--repeat", type=int, default=200, help="cached judge prompts to ask again (0 to skip)")
    g.add_argument("--report-only", action="store_true", help="score existing records; no calls")
    g.add_argument("--profile", choices=["strict", "core", "all"], default="core")
    g.add_argument("--concurrency", type=int, default=1, help="calls in flight at once")
    g.add_argument("--rate", type=float, default=10.0, help="most calls started per minute")
    g.add_argument("--max-wait", type=float, default=180, help="minutes to wait out a busy or unreachable endpoint")
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # the Windows console default can't print the tables

    if args.cmd == "judge":
        asyncio.run(cmd_judge(args))
    elif args.cmd == "gate1b":
        asyncio.run(cmd_gate1b(args))
    else:
        {"report": cmd_report, "members": cmd_members, "subsets": cmd_subsets}[args.cmd](args)


if __name__ == "__main__":
    main()
