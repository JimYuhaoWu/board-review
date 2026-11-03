"""Judge gates (task J3).

Gate 1: one published judge re-runs Martian's full pipeline on the stored `claude-code`
reviews, through our harness. Pass rule (fixed before any result): for at least 90% of golden
comments, found / not found agrees with Martian's published verdicts from the same judge, and
F1 (Core) is within 3 points of the published F1.

Gate 1b: a local judge re-judges the stored reviews of several published tools with
Martian's full pipeline (extraction, dedup, judging), and its ranking of those tools is
compared with the rankings of Martian's three published judges.

Pass rule (fixed before any result): the local judge's mean Kendall tau with the three
published judges is at least the lowest tau between two published judges.

The repeat check re-asks a fixed sample of cached judge prompts, bypassing the cache, and
reports how often the verdict is the same: temperature-0 repeatability in Gate 1b, and
model drift at later sessions.
"""

import json
import random
from itertools import combinations
from pathlib import Path

from tqdm import tqdm

from .data import RESULTS
from .llm import JudgeModel
from .martian import step2_extract_comments as m2
from .metrics import per_pr, summarize
from .pipeline import judge_slug
from .views import View, score

PUBLISHED = ["anthropic_claude-opus-4-5-20251101", "anthropic_claude-sonnet-4-5-20250929", "openai_gpt-5.2"]
GATE1B_TOOLS = ["cubic-v2", "augment", "devin", "macroscope", "claude-code", "codeant-v2", "kg"]
_ALL = View("all", "stored")


def kendall_tau(a: dict[str, float], b: dict[str, float]) -> float:
    """Kendall tau-a between two scorings of the same items."""
    items = sorted(set(a) & set(b))
    pairs = list(combinations(items, 2))
    if not pairs:
        return float("nan")
    s = 0
    for x, y in pairs:
        da, db = a[x] - a[y], b[x] - b[y]
        s += (da * db > 0) - (da * db < 0)
    return s / len(pairs)


def _slug(url: str) -> str:
    return url.split("github.com/")[-1].replace("/pull/", "__").replace("/", "__")


async def judge_stored(jm: JudgeModel, martian_dir: Path, tools: list[str], log=print) -> Path:
    """Judge the stored reviews of `tools` with the full pipeline; one record per review."""
    data = json.loads((martian_dir / "results" / "benchmark_data.json").read_text(encoding="utf-8"))
    out_dir = RESULTS / "stored" / judge_slug(jm.model)
    work = [(url, e, r) for url, e in data.items() for r in e["reviews"] if r["tool"] in tools]
    for url, entry, review in tqdm(work, desc=f"gate1b {jm.model}", unit="review"):
        out = out_dir / review["tool"] / f"{_slug(url)}.json"
        if out.exists():
            old = json.loads(out.read_text(encoding="utf-8"))
            if old["judge"]["fingerprint"] == jm.fingerprint and not old["dedup_fallback"]:
                continue
        golden = [{"comment": g["comment"], "severity": g.get("severity"), "category": g.get("category")}
                  for g in entry["golden_comments"]]
        body = m2.get_all_comment_text(review.get("review_comments", []))
        if body and len(body.strip()) >= 20:
            texts = await jm.extract(body)
        else:  # Martian's step 3 falls back to the raw comment bodies when there was nothing to extract
            texts = [c["body"] for c in review.get("review_comments", []) if c.get("body")]
        groups, fell_back = await jm.dedup(texts)
        verdicts = []
        for g in golden:
            row = []
            for t in texts:
                a = await jm.match(g["comment"], t)
                row.append({k: a.get(k) for k in ("match", "confidence", "reasoning")})
            verdicts.append(row)
        record = {
            "tool": review["tool"], "pr_url": url, "golden": golden,
            "candidates": [{"text": t, "finding": None, "member": None, "label": None} for t in texts],
            "groups": groups, "dedup_fallback": fell_back, "verdicts": verdicts,
            "judge": {"model": jm.model, "fingerprint": jm.fingerprint, "served": jm.served},
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return out_dir


def _published_f1(martian_dir: Path, tools: list[str], profile: str) -> dict[str, dict[str, float]]:
    out = {}
    for judge in PUBLISHED:
        ev = json.loads((martian_dir / "results" / judge / "evaluations.json").read_text(encoding="utf-8"))
        out[judge] = {
            t: summarize(per_pr({(pr, 1): r[t] for pr, r in ev.items() if t in r}, profile))["f1"] for t in tools
        }
    return out


def gate1b_report(judge: str, martian_dir: Path, tools: list[str], profile: str = "core") -> tuple[str, bool]:
    base = RESULTS / "stored" / judge_slug(judge)
    local, missing = {}, []
    for t in tools:
        records = [json.loads(f.read_text(encoding="utf-8")) for f in sorted((base / t).glob("*.json"))]
        if not records:
            missing.append(t)
            continue
        local[t] = summarize(per_pr({(r["pr_url"], 1): score(r, _ALL) for r in records}, profile))["f1"]
    pub = _published_f1(martian_dir, tools, profile)
    names = {j: j.split("_", 1)[1] for j in PUBLISHED}
    taus_local = {j: kendall_tau(local, pub[j]) for j in PUBLISHED}
    taus_pub = {(a, b): kendall_tau(pub[a], pub[b]) for a, b in combinations(PUBLISHED, 2)}
    mean_local = sum(taus_local.values()) / len(taus_local)
    floor = min(taus_pub.values())
    passed = not missing and mean_local >= floor

    lines = [f"## Gate 1b: {judge}, profile {profile}\n",
             "| Tool | " + " | ".join(names[j] for j in PUBLISHED) + f" | {judge} |",
             "|---|" + "---|" * (len(PUBLISHED) + 1)]
    for t in sorted(tools, key=lambda t: -pub[PUBLISHED[0]][t]):
        cells = [f"{pub[j][t]:.1%}" for j in PUBLISHED] + [f"{local[t]:.1%}" if t in local else "missing"]
        lines.append(f"| {t} | " + " | ".join(cells) + " |")
    lines += ["", "Kendall tau of the tool rankings (F1):", ""]
    lines += [f"- {judge} vs {names[j]}: {taus_local[j]:+.2f}" for j in PUBLISHED]
    lines += [f"- {names[a]} vs {names[b]}: {t:+.2f}" for (a, b), t in taus_pub.items()]
    lines += ["", f"Mean tau with the published judges {mean_local:+.2f}; lowest tau between two published judges {floor:+.2f}. "
              + ("**Pass.**" if passed else "**Fail.**" + (f" Missing tools: {', '.join(missing)}." if missing else ""))]
    return "\n".join(lines), passed


def gate1_report(judge: str, published: str, martian_dir: Path, tool: str = "claude-code",
                 profile: str = "core") -> tuple[str, bool]:
    records = {r["pr_url"]: r for r in (json.loads(f.read_text(encoding="utf-8"))
                                        for f in sorted((RESULTS / "stored" / judge_slug(judge) / tool).glob("*.json")))}
    ev = json.loads((martian_dir / "results" / published / "evaluations.json").read_text(encoding="utf-8"))
    ours = {url: score(r, _ALL) for url, r in records.items()}
    theirs = {url: e[tool] for url, e in ev.items() if tool in e and url in ours}
    same = total = 0
    for url, pub in theirs.items():
        if pub.get("skipped") or ours[url].get("skipped"):
            continue
        found_pub = {t["golden_comment"] for t in pub["true_positives"]}
        found_ours = {t["golden_comment"] for t in ours[url]["true_positives"]}
        for g in records[url]["golden"]:
            total += 1
            same += (g["comment"] in found_pub) == (g["comment"] in found_ours)
    agreement = same / total if total else float("nan")
    f1_ours = summarize(per_pr({(u, 1): r for u, r in ours.items() if u in theirs}, profile))["f1"]
    f1_pub = summarize(per_pr({(u, 1): r for u, r in theirs.items()}, profile))["f1"]
    passed = len(theirs) == len(ours) > 0 and agreement >= 0.90 and abs(f1_ours - f1_pub) <= 0.03
    lines = [
        f"## Gate 1: {judge} on the stored `{tool}` reviews, profile {profile}",
        "",
        f"- PRs judged: {len(ours)} (published: {len(theirs)})",
        f"- Golden comments with the same found / not found as the published {published}: "
        f"{same} of {total} ({agreement:.1%}); needed 90%",
        f"- F1: ours {f1_ours:.1%}, published {f1_pub:.1%} "
        f"(difference {f1_ours - f1_pub:+.1%}; allowed ±3.0 points)",
        "",
        "**Pass.**" if passed else "**Fail.**",
    ]
    text = "\n".join(lines)
    return text, passed


async def repeat_check(jm: JudgeModel, n: int = 200, seed: int = 0) -> dict:
    """Re-ask n cached judge prompts of this model and fingerprint, bypassing the cache."""
    rows = jm.cache.db.execute(
        "SELECT prompt, response FROM calls WHERE step = 'judge' AND model = ? AND fingerprint = ? AND prompt IS NOT NULL"
        " ORDER BY key", (jm.model, jm.fingerprint)).fetchall()
    sample = random.Random(seed).sample(rows, min(n, len(rows)))
    same = changed = failed = 0
    for prompt, response in tqdm(sample, desc=f"repeat {jm.model}", unit="call"):
        again = await jm._call(lambda p=prompt: jm.judge.call_llm(p))
        if "match" not in again:
            failed += 1
        elif bool(again["match"]) == bool(json.loads(response)["match"]):
            same += 1
        else:
            changed += 1
    asked = same + changed
    return {"sampled": len(sample), "same": same, "changed": changed, "failed": failed,
            "agreement": same / asked if asked else float("nan")}
