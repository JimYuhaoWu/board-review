"""PLAN v2, the consensus add-on: match the findings of k `/code-review` runs, keep issues m of n found.

The matching step is part of the add-on, so its errors count against it: agreement is decided only by
its groups, never by the judge's dedup. It is one model call per PR over all k runs; a smaller n uses
the same groups, restricted to its runs. A kept issue counts once, as a hit if any of its findings
matches a golden comment, so the add-on reports each issue with all its findings' wordings.
"""

import hashlib
import itertools
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable

from .metrics import bootstrap, per_pr, summarize
from .pipeline import SCHEMA_VERSION, concat_records, record_path
from .views import View, score

MATCH_PROMPT = """You are given the findings of {k} independent code reviews of one pull request.
Group the findings that report the same defect.

Two findings report the same defect only if fixing one would fix the other. Being in the same file, \
touching the same function, or sharing a theme is not enough. If you are unsure, keep them apart.
Findings from one review can also be duplicates of each other.

Findings:
{findings}

Answer with JSON only, in this form, where every finding number appears exactly once and a finding \
that matches nothing is a group of its own:
{{"groups": [[0, 3], [1], [2, 4]]}}"""

VIEW = View("consensus", "consensus", ungrouped=True, per_issue=True)
EFFORT = "medium"  # the review's session effort (PLAN v2, Phase B)
AskFn = Callable[[str, str], tuple[str, float]]


def consensus_variant(model: str) -> str:
    return "consensus-" + model.strip().replace("/", "_").replace(":", "_")


def ask_claude(prompt: str, model: str) -> tuple[str, float]:
    """One `claude -p` call with no tools, in an empty folder. Returns (reply, cost in USD)."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is set; matching must run on the plan's login")
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("claude is not on PATH")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_CODE_", "CLAUDECODE", "ANTHROPIC_"))}
    with tempfile.TemporaryDirectory() as cwd:
        done = subprocess.run(
            [exe, "-p", "--model", model, "--effort", EFFORT, "--output-format", "json", "--tools", "", "--strict-mcp-config",
             "--no-session-persistence"],
            input=prompt, cwd=cwd, env=env, capture_output=True, encoding="utf-8", errors="replace", timeout=900,
        )
    if done.returncode:
        raise RuntimeError(f"claude exited {done.returncode}: {(done.stdout or done.stderr)[:300]}")
    reply = json.loads(done.stdout)
    if reply.get("is_error"):
        raise RuntimeError(f"claude reported an error: {str(reply.get('result'))[:300]}")
    return reply["result"], reply.get("total_cost_usd", 0.0)


def parse_groups(text: str, n: int) -> list[list[int]]:
    """The groups in a matching reply; ValueError unless every finding 0..n-1 appears exactly once."""
    found = re.search(r"\{.*\}", text, re.S)
    if not found:
        raise ValueError("no JSON object in the reply")
    groups = json.loads(found.group(0))["groups"]
    if sorted(i for g in groups for i in g) != list(range(n)):
        raise ValueError(f"groups do not cover findings 0..{n - 1} exactly once")
    return groups


def match_runs(records: list[dict], model: str, ask: AskFn = ask_claude, retries: int = 3) -> dict:
    """The consensus record of one PR's k runs: their candidates and verdicts, and the matching step's groups."""
    candidates, findings, verdicts = concat_records(records)
    first = records[0]
    listing = "\n".join(f"{i}. (review {c['finding'].split('/')[0][1:]}) {c['text']}" for i, c in enumerate(candidates))
    prompt = MATCH_PROMPT.format(k=len(records), findings=listing)
    cost, groups, error = 0.0, [], None
    if candidates:
        for _ in range(retries):
            reply, spent = ask(prompt, model)
            cost += spent
            try:
                groups = parse_groups(reply, len(candidates))
                break
            except (ValueError, KeyError, TypeError) as e:
                error = e
        else:
            raise ValueError(f"{first['pr_id']}: no valid matching after {retries} tries: {error}")
    return {
        "schema_version": SCHEMA_VERSION,
        "dataset": first["dataset"],
        "variant": consensus_variant(model),
        "pr_id": first["pr_id"],
        "repeat": len(records),
        "merged_from": [r["repeat"] for r in records],
        "source_sha256": hashlib.sha256("".join(r["source_sha256"] for r in records).encode()).hexdigest(),
        "judge": first["judge"],
        "golden": first["golden"],
        "candidates": candidates,
        "findings": findings,
        "groups": groups,
        "dedup_fallback": False,
        "verdicts": verdicts,
        "run_meta": [r["run_meta"] for r in records],
        "matching": {"model": model, "effort": EFFORT, "prompt_sha256": hashlib.sha256(MATCH_PROMPT.encode()).hexdigest(),
                     "cost_usd": cost},
    }


def write_record(record: dict) -> None:
    out = record_path(record["dataset"], record["judge"]["model"], record["variant"], record["pr_id"], record["repeat"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")


def run_of(record: dict) -> list[int]:
    return [int(c["finding"].split("/")[0][1:]) for c in record["candidates"]]


def kept(record: dict, runs: tuple[int, ...], m: int) -> dict:
    """The record reduced to the issues at least m of `runs` found, and to those runs' findings."""
    of = run_of(record)
    keep = []
    for g in record["groups"]:
        inside = [i for i in g if of[i] in runs]
        if len({of[i] for i in inside}) >= m:
            keep += inside
    keep.sort()
    pos = {old: new for new, old in enumerate(keep)}
    ids = {record["candidates"][i]["finding"] for i in keep}
    return {
        **record,
        "candidates": [record["candidates"][i] for i in keep],
        "verdicts": [[row[i] for i in keep] for row in record["verdicts"]],
        "findings": [f for f in record["findings"] if f["id"] in ids],
        "groups": [g2 for g2 in ([pos[i] for i in g if i in pos] for g in record["groups"]) if g2],
    }


def m_of_n(records: list[dict]) -> dict[tuple[int, int], dict]:
    """{(m, n): {(pr key, subset number): result}} for every m ≤ n ≤ k over every subset of n runs."""
    table: dict[tuple[int, int], dict] = {}
    for rec in records:
        key = f"{rec['dataset']}/{rec['pr_id']}"
        runs = rec["merged_from"]
        for n in range(1, len(runs) + 1):
            for si, subset in enumerate(itertools.combinations(runs, n)):
                for m in range(1, n + 1):
                    table.setdefault((m, n), {})[(key, si)] = score(kept(rec, subset, m), VIEW)
    return table


def choose(units: dict[tuple[int, int], dict], n_boot: int = 2000, seed: int = 20261109) -> dict:
    """PLAN v2's selection rule: recall at least 90% of one run's, then the fewest false positives per PR,
    then the fewest runs whose false positives are within one bootstrap SE of that."""
    floor = 0.9 * summarize(units[(1, 1)])["recall"]
    stats = {c: summarize(u) for c, u in units.items()}
    eligible = [c for c, s in stats.items() if s["recall"] >= floor]
    best = min(eligible, key=lambda c: (stats[c]["fp_per_pr"], c[1]))
    near = [c for c in eligible
            if c == best or stats[c]["fp_per_pr"] - stats[best]["fp_per_pr"]
            <= bootstrap(units[c], units[best], "fp_per_pr", n_boot, seed)["se"]]
    chosen = min(near, key=lambda c: (c[1], stats[c]["fp_per_pr"]))
    return {"floor": floor, "eligible": eligible, "fewest_fp": best, "chosen": chosen}


def report(records: list[dict], profile: str = "core", n_boot: int = 2000) -> tuple[str, dict]:
    """The m-of-n table and the selection, as markdown, and the table's numbers for the figure."""
    units = {c: per_pr(r, profile) for c, r in m_of_n(records).items()}
    one = units[(1, 1)]
    one_recall = summarize(one)["recall"]
    run_costs = [m["total_cost_usd"] for rec in records for m in rec["run_meta"] if "total_cost_usd" in m]
    cost_run = sum(run_costs) / len(run_costs) if run_costs else 0.0
    cost_match = sum(r["matching"]["cost_usd"] for r in records) / len(records)
    picked = choose(units, n_boot)
    lines = [f"{len(records)} PRs, {records[0]['repeat']} runs each; one run ${cost_run:.3f}, matching ${cost_match:.3f} per PR.",
             "",
             "| Keep issues found by | Precision | Recall | FP per PR | ΔFP vs one run (95% CI) | Recall vs one run | Cost per PR |",
             "|---|---|---|---|---|---|---|"]
    numbers = []
    for m, n in sorted(units, key=lambda c: (c[1], c[0])):
        s = summarize(units[(m, n)])
        cost = n * cost_run + (cost_match if n > 1 else 0.0)
        delta = ""
        if (m, n) != (1, 1):
            b = bootstrap(units[(m, n)], one, "fp_per_pr", n_boot, 20261109)
            delta = f"{b['point']:+.2f} ({b['ci95'][0]:+.2f} to {b['ci95'][1]:+.2f})"
        mark = " **(chosen)**" if (m, n) == picked["chosen"] else ""
        relative = 100 * s["recall"] / one_recall if one_recall else 0.0
        lines.append(f"| {m} of {n}{mark} | {100 * s['precision']:.1f}% | {100 * s['recall']:.1f}% | {s['fp_per_pr']:.2f} "
                     f"| {delta} | {relative:.0f}% | ${cost:.2f} |")
        numbers.append({"m": m, "n": n, "precision": s["precision"], "recall": s["recall"],
                        "fp_per_pr": s["fp_per_pr"], "cost": cost})
    fewest, chosen = picked["fewest_fp"], picked["chosen"]
    lines += ["", f"Recall floor {100 * picked['floor']:.1f}%. Fewest false positives per PR above it: "
                  f"{fewest[0]} of {fewest[1]}. Chosen: {chosen[0]} of {chosen[1]}."]
    return "\n".join(lines), {"table": numbers, "selection": {k: picked[k] for k in ("floor", "fewest_fp", "chosen")}}
