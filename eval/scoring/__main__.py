"""Scoring command line. Run from the repo root:

    uv run python -m eval.scoring judge   --dataset martian --judge deepseek-flash --split dev
    uv run python -m eval.scoring report  --dataset martian --judge deepseek-flash --split dev
    uv run python -m eval.scoring members --dataset martian --judge deepseek-flash --split dev
    uv run python -m eval.scoring subsets --dataset martian --judge deepseek-flash --split dev
    uv run python -m eval.scoring gate2   --dataset martian --judge deepseek-flash --split dev
    uv run python -m eval.scoring gate3   --dataset qodo --judge deepseek-flash --split test --release "Gate 3"
    uv run python -m eval.scoring merge   --dataset qodo --judge deepseek-flash --split dev --pairs 1,2
    uv run python -m eval.scoring judge   --dataset qodo --judge deepseek-flash --split test --release "Gate 4" \n        --variants board-none,board-default,code-review-b1
    uv run python -m eval.scoring gate4   --dataset qodo --judge deepseek-flash --split test --release "Gate 4"

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

from .board import label_stats, member_cards, member_stats, members_in, select_default
from .gates import GATE1B_TOOLS
from .data import ROOT, iter_runs, load_dataset, member_categories, subagent_usage
from .llm import EndpointUnavailable, JudgeModel, LLMError
from .metrics import bootstrap, category_recall, per_pr, summarize
from .pipeline import MERGED, RESULTS, judge_run, judge_slug, load_records, merge_records
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


async def cmd_merge(args) -> None:
    """Merge judged `/code-review` repeats into `code-review-x2` records (PLAN Gate 3, A1).

    `--pairs 1,2 3,4` makes merged repeat 1 from repeats 1 and 2, and merged repeat 2 from 3 and 4.
    The only new judge call per merged record is Martian's dedup over the merged candidates.
    """
    ids = pr_ids(args)
    pairs = [[int(x) for x in p.split(",")] for p in args.pairs]
    jm = JudgeModel(args.judge, concurrency=args.concurrency, rate=args.rate, max_wait=args.max_wait * 60, log=tqdm.write)
    try:
        await jm.identify()
    except (LLMError, EndpointUnavailable) as e:
        print(f"{args.judge}: can't use the endpoint: {e}")
        return
    made, missing, shared = 0, [], 0
    for pid in ids:
        by_repeat = {r["repeat"]: r for r in load_records(args.dataset, args.judge, "code-review", [pid])}
        for k, pair in enumerate(pairs, start=1):
            if not all(n in by_repeat for n in pair):
                missing.append(f"{pid} {pair}")
                continue
            out = await merge_records([by_repeat[n] for n in pair], jm, k)
            rec = json.loads(out.read_text(encoding="utf-8"))
            run_of = [c["finding"].split("/")[0] for c in rec["candidates"]]
            shared += sum(1 for g in rec["groups"] or [] if len({run_of[i] for i in g}) > 1)
            made += 1
    print(f"{made} {MERGED} records; {shared} issues found by more than one run, each counted once; "
          f"{jm.calls} new judge calls")
    if missing:
        print("Missing repeats: " + "; ".join(missing))


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
    baseline = {(r["pr_id"], r["repeat"]): score(r, parse_view("code-review"))
                for r in load_records(args.dataset, args.judge, "code-review", ids)}
    usage = Counter()
    for r in records:
        stream = RESULTS / args.dataset / "board-full" / r["pr_id"] / f"r{r['repeat']}" / "stream.jsonl"
        if stream.exists():
            usage.update(subagent_usage(stream))
    cards = member_cards(records, baseline, {m: member_categories(m) for m in members_in(records)}, usage,
                         args.profile, args.labels, args.bootstrap)
    if args.member:
        cards = [c for c in cards if c["member"] in args.member.split(",")]
    print(f"\n**Member cards** (PLAN 6.5). Own categories: golden comments in the categories the brief targets, "
          f"found by the member / by `/code-review` on the same PRs / in total, all profiles. Unique: golden comments "
          f"no other member found, summed over runs. Marginal recall (primary) and F1 contribution: the board's value "
          f"minus its value without the member (positive helps), paired bootstrap over {len({r['pr_id'] for r in records})} PRs.\n")
    print("| Member | Runs | Own categories | Found: member / `/code-review` / total | Unique | Marginal recall (95% CI) "
          "| F1 contribution (95% CI) | Usage share |")
    print("|---|---|---|---|---|---|---|---|")
    for c in cards:
        lo, hi = c["ci95"]
        rlo, rhi = c["recall_ci95"]
        print(f"| {c['member']} | {c['runs']}/{len(records)} | {', '.join(c['categories']) or 'none'} | "
              f"{c['own_found']:g} / {c['own_found_baseline']:g} / {c['own_total']} | {c['unique']} | "
              f"{c['marginal_recall']:+.1%} ({rlo:+.1%} to {rhi:+.1%}) | "
              f"{c['contribution']:+.1%} ({lo:+.1%} to {hi:+.1%}) | {c['usage_share']:.0%} |")
    if usage.get("verifier"):
        print(f"\nVerifiers: {usage['verifier'] / sum(usage.values()):.0%} of the usage.")
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


def _martian_dir(args) -> Path:
    martian = Path(args.martian_dir or os.environ.get("MARTIAN_DIR", ""))
    if not (martian / "results" / "benchmark_data.json").exists():
        sys.exit("Point --martian-dir (or MARTIAN_DIR) at the pinned Martian clone's offline/ folder.")
    return martian


async def _judge_gate(args, martian: Path, tools: list[str], repeat: int = 0) -> tuple[bool, dict | None]:
    """Judge the stored reviews; returns (finished, repeat-check result). Reports run after the loop closes."""
    from .gates import judge_stored, repeat_check

    jm = JudgeModel(args.judge, concurrency=args.concurrency, rate=args.rate, max_wait=args.max_wait * 60,
                    log=tqdm.write)
    try:
        served = await jm.identify()
        print(f"{dt.datetime.now():%H:%M} judge {args.judge}: fingerprint {jm.fingerprint}, served {json.dumps(served)}")
        await judge_stored(jm, martian, tools)
        result = await repeat_check(jm, repeat) if repeat else None
    except EndpointUnavailable as e:
        print(f"STOPPED: {e}")
        print(f"{jm.calls} calls this session. Finished calls are cached; re-run to continue.")
        return False, None
    print(f"{dt.datetime.now():%H:%M} done: {jm.calls} calls this session")
    return True, result


def cmd_gate1(args) -> None:
    from .gates import gate1_report

    martian = _martian_dir(args)
    if not args.report_only and not asyncio.run(_judge_gate(args, martian, [args.tool]))[0]:
        return
    text, _ = gate1_report(args.judge, args.published, martian, args.tool, args.profile)
    print(text)
    report = ROOT / "eval" / "reports" / f"gate1-{judge_slug(args.judge)}.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(text + "\n", encoding="utf-8")
    print(f"\nWritten to {report.relative_to(ROOT)}")


def cmd_gate1b(args) -> None:
    from .gates import gate1b_report

    martian = _martian_dir(args)
    tools = args.tools.split(",")
    report = ROOT / "eval" / "reports" / f"gate1b-{judge_slug(args.judge)}.md"
    repeat = None
    if not args.report_only:
        finished, repeat = asyncio.run(_judge_gate(args, martian, tools, args.repeat))
        if not finished:
            return
    text, passed = gate1b_report(args.judge, martian, tools, args.profile)
    if repeat:
        text += (f"\n\nRepeat check: {repeat['sampled']} cached judge prompts asked again; same verdict "
                 f"{repeat['same']}, changed {repeat['changed']}, failed {repeat['failed']} "
                 f"(agreement {repeat['agreement']:.1%}).")
    print(text)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(text + "\n", encoding="utf-8")
    print(f"\nWritten to {report.relative_to(ROOT)}")


GATE2_REPEATS = 2


def cmd_gate2(args) -> None:
    """PLAN Phase 3, Gate 2 as pre-registered on 2026-11-04: the full board's recall
    (CONFIRMED + PLAUSIBLE) must be higher than `/code-review`'s on the pilot PRs, with at
    least two repeats per PR for each. Precision, F1, and the grouping loss are reported
    as guardrails; they don't decide the gate."""
    ids = pr_ids(args)
    names = ["board:full:cp", "code-review", "board:full:cp:ungrouped"]
    res = {n: _results(args, parse_view(n), ids) for n in names}
    units = {n: per_pr(r, args.profile) for n, r in res.items()}
    print(f"\n## Gate 2: {args.dataset}, split {args.split}, judge {args.judge}, profile {args.profile}\n")
    print("| View | PRs | Runs | Precision | Recall | F1 |")
    print("|---|---|---|---|---|---|")
    for n in names:
        s = summarize(units[n])
        print(f"| {n} | {s['prs']} | {len(res[n])} | {s['precision']:.1%} | {s['recall']:.1%} | {s['f1']:.1%} |")
    board, cr = units["board:full:cp"], units["code-review"]
    rows = [("ΔRecall (decides)", bootstrap(board, cr, "recall", n=args.bootstrap)),
            ("ΔPrecision", bootstrap(board, cr, "precision", n=args.bootstrap)),
            ("ΔF1", bootstrap(board, cr, "f1", n=args.bootstrap))]
    print("\n| Paired bootstrap, board − `/code-review` | Point | 95% CI | p |")
    print("|---|---|---|---|")
    for label, b in rows:
        pv = "n/a" if b.get("p_two_sided") is None else format(b["p_two_sided"], ".3f")
        print(f"| {label} | {b['point']:+.1%} | {b['ci95'][0]:+.1%} to {b['ci95'][1]:+.1%} | {pv} |")
    loss = summarize(units["board:full:cp:ungrouped"])["tp"] - summarize(board)["tp"]
    print(f"\nGrouping loss: {loss:g} golden comments matched by a finding but missing from the report.")
    short = {}
    scored = {pid for n in ("board:full:cp", "code-review") for pid, _ in res[n]}  # the pilot PRs that were run
    for n in ("board:full:cp", "code-review"):
        per = Counter(pid for pid, _ in res[n])
        missing = sorted(pid for pid in scored if per.get(pid, 0) < GATE2_REPEATS)
        if missing:
            short[n] = missing
    if short:
        print(f"\nNOT DECIDED: the rule needs {GATE2_REPEATS} repeats per PR; too few for "
              + "; ".join(f"{n} ({', '.join(v)})" for n, v in short.items()))
        return
    passed = rows[0][1]["point"] > 0
    print(f"\n**Gate 2: {'PASS' if passed else 'FAIL'}** (board recall {'>' if passed else '≤'} `/code-review` recall)")


GATE3_VIEWS = {"A2": "board-cr:full:cp:issues", "A1": "code-review-x2", "single": "code-review:issues"}


def cmd_gate3(args) -> None:
    """PLAN Phase 3, Gate 3 (Plan A): board + `/code-review` (A2) against `/code-review` run twice
    and merged (A1), both counted per issue. Recall decides; A2's F1 must be at least one
    `/code-review` run's."""
    ids = pr_ids(args)
    res = {k: _results(args, parse_view(v), ids) for k, v in GATE3_VIEWS.items()}
    both = sorted({pid for pid, _ in res["A2"]} & {pid for pid, _ in res["A1"]})
    units = {k: {p: u for p, u in per_pr(r, args.profile).items() if p in both} for k, r in res.items()}
    print(f"\n## Gate 3: {args.dataset}, split {args.split}, judge {args.judge}, profile {args.profile}\n")
    print("| Variant | View | PRs | Runs | Precision | Recall | F1 |")
    print("|---|---|---|---|---|---|---|")
    for k, v in GATE3_VIEWS.items():
        s = summarize(units[k])
        runs = sum(1 for pid, _ in res[k] if pid in both)
        print(f"| {k} | {v} | {s['prs']} | {runs} | {s['precision']:.1%} | {s['recall']:.1%} | {s['f1']:.1%} |")
    rows = [("ΔRecall (decides)", bootstrap(units["A2"], units["A1"], "recall", n=args.bootstrap)),
            ("ΔPrecision", bootstrap(units["A2"], units["A1"], "precision", n=args.bootstrap)),
            ("ΔF1", bootstrap(units["A2"], units["A1"], "f1", n=args.bootstrap))]
    print("\n| Paired bootstrap, A2 − A1 | Point | 95% CI | p |")
    print("|---|---|---|---|")
    for label, b in rows:
        pv = "n/a" if b.get("p_two_sided") is None else format(b["p_two_sided"], ".3f")
        print(f"| {label} | {b['point']:+.1%} | {b['ci95'][0]:+.1%} to {b['ci95'][1]:+.1%} | {pv} |")
    f1 = {k: summarize(units[k])["f1"] for k in units}
    guard = f1["A2"] >= f1["single"]
    print(f"\nGuardrail: A2 F1 {f1['A2']:.1%} {'≥' if guard else '<'} one `/code-review` run's {f1['single']:.1%}.")
    short = [f"{k} {pid}" for k in ("A2", "A1") for pid in both
             if sum(1 for p, _ in res[k] if p == pid) < GATE2_REPEATS]
    if not both or short:
        print("\nNOT DECIDED: the rule needs 2 repeats per PR for A1 and A2; short: " + (", ".join(short) or "no PRs"))
        return
    lo, hi = rows[0][1]["ci95"]
    if lo > 0 and guard:
        verdict = "WIN (board + `/code-review` finds more than `/code-review` twice)"
    elif hi < 0:
        verdict = "MEMBERS NOT NEEDED (`/code-review` twice finds more)"
    else:
        verdict = "NO DECISION (" + ("the recall CI spans 0" if lo <= 0 else "the F1 guardrail fails") + ")"
    print(f"\n**Gate 3: {verdict}**")


GATE4_VIEWS = {"B2": "board-default:full:cp:issues", "B1": "board-none:full:confirmed:issues",
               "floor ref": "code-review-b1:issues"}
GATE4_REPORTED = {"B1, CONFIRMED + PLAUSIBLE": "board-none:full:cp:issues",
                  "B2, CONFIRMED only": "board-default:full:confirmed:issues"}
GATE4_FLOOR = 0.9  # B2's recall must be at least 90% of the floor reference's (point estimates)


def gate4_verdict(ci: tuple[float, float], floor_met: bool) -> str:
    """PLAN Phase 3, Gate 4 rule: false positives per PR, B2 − B1, decides; the recall floor guards."""
    lo, hi = ci
    if hi < 0 and floor_met:
        return "BOARD WINS (fewer false positives per PR, recall floor met)"
    if lo > 0:
        return "CHECKING IS ENOUGH (the checked `/code-review` has fewer false positives per PR)"
    return "NO DECISION (" + ("the recall floor fails" if hi < 0 else "the false-positive CI spans 0") + ")"


def cmd_gate4(args) -> None:
    """PLAN Phase 3, Gate 4: the default board (B2) against `/code-review` checked by the board's
    verifier (B1), both counted per issue. False positives per PR decide; B2's recall must be at
    least 90% of the recall of B1's own `/code-review` sessions, judged alone before the check."""
    ids = pr_ids(args)
    views = {**GATE4_VIEWS, **GATE4_REPORTED}
    res = {k: _results(args, parse_view(v), ids) for k, v in views.items()}
    both = sorted(set.intersection(*({pid for pid, _ in res[k]} for k in GATE4_VIEWS)))
    units = {k: {p: u for p, u in per_pr(r, args.profile).items() if p in both} for k, r in res.items()}
    print(f"\n## Gate 4: {args.dataset}, split {args.split}, judge {args.judge}, profile {args.profile}\n")
    print("| Variant | View | PRs | Runs | Precision | Recall | F1 | FP per PR |")
    print("|---|---|---|---|---|---|---|---|")
    for k, v in views.items():
        s = summarize(units[k], both) if both else summarize({})
        runs = sum(1 for pid, _ in res[k] if pid in both)
        print(f"| {k} | {v} | {s['prs']} | {runs} | {s['precision']:.1%} | {s['recall']:.1%} | {s['f1']:.1%} "
              f"| {s['fp_per_pr']:.2f} |")
    short = [f"{k} {pid}" for k in GATE4_VIEWS for pid in both
             if sum(1 for p, _ in res[k] if p == pid) < GATE2_REPEATS]
    if not both or short:
        print("\nNOT DECIDED: the rule needs 2 repeats per PR for B1, B2 and B1's `/code-review` sessions; short: "
              + (", ".join(short) or "no PRs"))
        return
    fp = bootstrap(units["B2"], units["B1"], "fp_per_pr", n=args.bootstrap)
    rows = [("ΔFP per PR (decides)", fp, ".2f"),
            ("ΔRecall", bootstrap(units["B2"], units["B1"], "recall", n=args.bootstrap), ".1%"),
            ("ΔPrecision", bootstrap(units["B2"], units["B1"], "precision", n=args.bootstrap), ".1%")]
    print("\n| Paired bootstrap, B2 − B1 | Point | 95% CI | p |")
    print("|---|---|---|---|")
    for label, b, f in rows:
        pv = "n/a" if b.get("p_two_sided") is None else format(b["p_two_sided"], ".3f")
        print(f"| {label} | {b['point']:+{f}} | {b['ci95'][0]:+{f}} to {b['ci95'][1]:+{f}} | {pv} |")
    r2, ref = summarize(units["B2"])["recall"], summarize(units["floor ref"])["recall"]
    floor_met = r2 >= GATE4_FLOOR * ref
    print(f"\nRecall floor: B2 {r2:.1%} {'≥' if floor_met else '<'} 90% of B1's `/code-review` sessions' "
          f"{ref:.1%} ({GATE4_FLOOR * ref:.1%}).")
    print(f"\n**Gate 4: {gate4_verdict(fp['ci95'], floor_met)}**")


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m eval.scoring")
    sub = p.add_subparsers(dest="cmd", required=True)
    g4 = sub.add_parser("gate4", help="default board vs /code-review checked by the verifier (PLAN Phase 3, Gate 4)")
    g4.add_argument("--dataset", required=True)
    g4.add_argument("--judge", required=True)
    g4.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    g4.add_argument("--release", help="why test PRs are being scored; required for test or all (PLAN 6.6)")
    g4.add_argument("--profile", choices=["strict", "core", "all"], default="core")
    g4.add_argument("--bootstrap", type=int, default=2000, help="bootstrap resamples")
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
    r.add_argument("--views", nargs="+", help="code-review | code-review@whole | board-full@whole | board:<full|default|id,id>:<confirmed|cp|raw>[:ungrouped]")
    r.add_argument("--compare", nargs="+", help="pairs A,B for the paired bootstrap")
    r.add_argument("--categories", action="store_true", help="recall per golden category")
    for name in ("members", "subsets"):
        sub.choices[name].add_argument("--labels", choices=["confirmed", "cp", "raw"], default="cp")
    sub.choices["subsets"].add_argument("--top", type=int, default=15)
    sub.choices["members"].add_argument("--member", help="comma-separated ids: print only their cards")
    g2 = sub.add_parser("gate2", help="full-board recall vs /code-review on the pilot PRs (PLAN Phase 3)")
    g2.add_argument("--dataset", required=True)
    g2.add_argument("--judge", required=True)
    g2.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    g2.add_argument("--release", help="why test PRs are being scored; required for test or all (PLAN 6.6)")
    g2.add_argument("--profile", choices=["strict", "core", "all"], default="core")
    g2.add_argument("--bootstrap", type=int, default=2000, help="bootstrap resamples")
    mg = sub.add_parser("merge", help="merge /code-review repeats into code-review-x2 records (PLAN Gate 3)")
    mg.add_argument("--dataset", required=True)
    mg.add_argument("--judge", required=True)
    mg.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    mg.add_argument("--release", help="why test PRs are being scored; required for test or all (PLAN 6.6)")
    mg.add_argument("--pairs", nargs="+", default=["1,2"], help="repeats to merge, one group per merged repeat")
    mg.add_argument("--concurrency", type=int, default=1, help="calls in flight at once")
    mg.add_argument("--rate", type=float, default=10.0, help="most calls started per minute")
    mg.add_argument("--max-wait", type=float, default=180, help="minutes to wait out a busy or unreachable endpoint")
    g3 = sub.add_parser("gate3", help="board + /code-review vs /code-review twice (PLAN Phase 3, Gate 3)")
    g3.add_argument("--dataset", required=True)
    g3.add_argument("--judge", required=True)
    g3.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    g3.add_argument("--release", help="why test PRs are being scored; required for test or all (PLAN 6.6)")
    g3.add_argument("--profile", choices=["strict", "core", "all"], default="core")
    g3.add_argument("--bootstrap", type=int, default=2000, help="bootstrap resamples")
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
    g1 = sub.add_parser("gate1", help="our pipeline vs a published judge's verdicts (PLAN Phase 3)")
    g1.add_argument("--judge", required=True, help="for example openrouter:anthropic/claude-sonnet-4.5")
    g1.add_argument("--published", default="anthropic_claude-sonnet-4-5-20250929",
                    help="Martian results folder of the same judge")
    g1.add_argument("--tool", default="claude-code")
    g1.add_argument("--martian-dir", help="the pinned Martian clone's offline/ folder (or set MARTIAN_DIR)")
    g1.add_argument("--report-only", action="store_true", help="score existing records; no calls")
    g1.add_argument("--profile", choices=["strict", "core", "all"], default="core")
    g1.add_argument("--concurrency", type=int, default=4, help="calls in flight at once")
    g1.add_argument("--rate", type=float, default=60.0, help="most calls started per minute")
    g1.add_argument("--max-wait", type=float, default=30, help="minutes to wait out a busy or unreachable endpoint")
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # the Windows console default can't print the tables

    if args.cmd == "judge":
        asyncio.run(cmd_judge(args))
    elif args.cmd == "merge":
        asyncio.run(cmd_merge(args))
    elif args.cmd == "gate1b":
        cmd_gate1b(args)
    elif args.cmd == "gate1":
        cmd_gate1(args)
    else:
        {"report": cmd_report, "members": cmd_members, "subsets": cmd_subsets, "gate2": cmd_gate2,
         "gate3": cmd_gate3, "gate4": cmd_gate4}[args.cmd](args)


if __name__ == "__main__":
    main()
