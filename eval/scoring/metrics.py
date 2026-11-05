"""Metrics over scored reviews: Martian profiles, F-beta, per-category recall, and the
paired bootstrap over PRs.

Counting follows Martian's score_profiles: true positives and false negatives are
filtered by the golden comment's category, false positives are not. Counts are summed
over PRs (micro average). Repeats of one PR are averaged first, so every PR weighs the same.
"""

import random
from collections import defaultdict

from .martian.score_profiles import _fbeta
from .profiles import PROFILE_CATEGORIES

Counts = tuple[float, float, float]  # tp, fp, fn


def counts(result: dict, profile: str) -> Counts:
    cats = PROFILE_CATEGORIES[profile]
    tp = sum(1 for t in result["true_positives"] if t.get("category") in cats)
    fn = sum(1 for t in result["false_negatives"] if t.get("category") in cats)
    return tp, result["fp"], fn


def per_pr(results: dict[tuple[str, int], dict], profile: str) -> dict[str, Counts]:
    """{(pr_id, repeat): result} -> {pr_id: counts averaged over repeats}."""
    by_pr: dict[str, list[Counts]] = defaultdict(list)
    for (pid, _), r in results.items():
        if not r.get("skipped"):
            by_pr[pid].append(counts(r, profile))
    return {pid: tuple(sum(c[i] for c in cs) / len(cs) for i in range(3)) for pid, cs in by_pr.items()}


def summarize(units: dict[str, Counts], prs: list[str] | None = None) -> dict:
    prs = list(units) if prs is None else prs
    tp = sum(units[p][0] for p in prs)
    fp = sum(units[p][1] for p in prs)
    fn = sum(units[p][2] for p in prs)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "prs": len(prs),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": _fbeta(precision, recall, 1.0),
        "f2": _fbeta(precision, recall, 2.0),
    }


def category_recall(results: dict[tuple[str, int], dict]) -> dict[str, tuple[float, float]]:
    """{category: (found, total)}, averaged over repeats like per_pr."""
    by_pr: dict[str, list[dict[str, list[int]]]] = defaultdict(list)
    for (pid, _), r in results.items():
        if r.get("skipped"):
            continue
        c: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for t in r["true_positives"]:
            c[t.get("category")][0] += 1
            c[t.get("category")][1] += 1
        for t in r["false_negatives"]:
            c[t.get("category")][1] += 1
        by_pr[pid].append(c)
    out: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for reps in by_pr.values():
        for c in reps:
            for cat, (found, total) in c.items():
                out[cat][0] += found / len(reps)
                out[cat][1] += total / len(reps)
    return {cat: (v[0], v[1]) for cat, v in sorted(out.items(), key=lambda kv: str(kv[0]))}


def bootstrap(
    a: dict[str, Counts],
    b: dict[str, Counts] | None = None,
    metric: str = "f1",
    n: int = 2000,
    seed: int = 0,
) -> dict:
    """Resample PRs with replacement. With b, the same resamples give the paired
    difference a - b over the PRs both have."""
    prs = sorted(set(a) & set(b)) if b is not None else sorted(a)
    if not prs:
        return {"prs": 0}
    rng = random.Random(seed)
    stats = []
    for _ in range(n):
        sample = [rng.choice(prs) for _ in prs]
        value = summarize(a, sample)[metric]
        if b is not None:
            value -= summarize(b, sample)[metric]
        stats.append(value)
    stats.sort()
    mean = sum(stats) / n
    se = (sum((s - mean) ** 2 for s in stats) / (n - 1)) ** 0.5
    point = summarize(a, prs)[metric] - (summarize(b, prs)[metric] if b is not None else 0.0)
    out = {"prs": len(prs), "point": point, "se": se, "ci95": (stats[int(0.025 * n)], stats[int(0.975 * n) - 1])}
    if b is not None:
        below = sum(1 for s in stats if s <= 0) / n
        above = sum(1 for s in stats if s >= 0) / n
        # With no spread (for example a single PR) the bootstrap can't give a p-value.
        out["p_two_sided"] = min(1.0, 2 * min(below, above)) if stats[0] != stats[-1] else None
    return out
