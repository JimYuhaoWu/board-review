"""Board-specific analyses on the development PRs (PLAN Phase 4): member statistics,
label checks, and the default-board selection rule.

Member and label statistics count a candidate as matched when the judge matched it to
any golden comment in the profile. This is a diagnostic: it skips the tie-breaking in
Martian's evaluate_review, which marks only each golden comment's best candidate.
"""

from collections import defaultdict
from itertools import combinations

from .martian.score_profiles import PROFILE_CATEGORIES
from .metrics import bootstrap, per_pr, summarize
from .views import LABEL_FILTERS, View, score


def _matches(record: dict, profile: str) -> list[set[int]]:
    """For each candidate, the golden comments (indices) in the profile it matched."""
    cats = PROFILE_CATEGORIES[profile]
    out = [set() for _ in record["candidates"]]
    for gi, (g, row) in enumerate(zip(record["golden"], record["verdicts"], strict=True)):
        if g.get("category") not in cats:
            continue
        for ci, v in enumerate(row):
            if v.get("match"):
                out[ci].add(gi)
    return out


def members_in(records: list[dict]) -> list[str]:
    seen = []
    for r in records:
        for m in r.get("members", {}).get("ran", []) + [c["member"] for c in r["candidates"]]:
            if m and m not in seen:
                seen.append(m)
    return seen


def member_stats(records: list[dict], profile: str, labels: str = "cp") -> list[dict]:
    """Per member, summed over runs: golden comments found, found by no other member,
    candidates, and candidates matching nothing (the false positives it adds)."""
    allowed = LABEL_FILTERS[labels]
    stats = defaultdict(lambda: {"found": 0, "unique": 0, "candidates": 0, "unmatched": 0})
    for r in records:
        matches = _matches(r, profile)
        finders = defaultdict(set)
        for c, m in zip(r["candidates"], matches, strict=True):
            if allowed is not None and c["label"] not in allowed:
                continue
            s = stats[c["member"]]
            s["candidates"] += 1
            s["unmatched"] += not m
            for gi in m:
                finders[gi].add(c["member"])
        for gi, who in finders.items():
            for member in who:
                stats[member]["found"] += 1
            if len(who) == 1:
                stats[next(iter(who))]["unique"] += 1
    rows = []
    for member in members_in(records):
        s = stats[member]
        s["precision"] = (s["candidates"] - s["unmatched"]) / s["candidates"] if s["candidates"] else 0.0
        rows.append({"member": member, **s})
    return rows


def label_stats(records: list[dict], profile: str) -> dict:
    """Finding precision per label, the wrongful-refutation rate, and golden comments
    that the raw union found but CONFIRMED + PLAUSIBLE lost."""
    per_label = {label: {"findings": 0, "matched": 0} for label in ("CONFIRMED", "PLAUSIBLE", "REFUTED")}
    lost = 0
    for r in records:
        matches = _matches(r, profile)
        by_finding = defaultdict(set)
        for c, m in zip(r["candidates"], matches, strict=True):
            by_finding[c["finding"]] |= m
        for f in r.get("findings", []):
            if f["label"] in per_label:
                per_label[f["label"]]["findings"] += 1
                per_label[f["label"]]["matched"] += bool(by_finding.get(f["id"]))
        raw = set().union(*matches) if matches else set()
        kept = set().union(*(m for c, m in zip(r["candidates"], matches, strict=True)
                             if c["label"] in LABEL_FILTERS["cp"])) if matches else set()
        lost += len(raw - kept)
    for s in per_label.values():
        s["precision"] = s["matched"] / s["findings"] if s["findings"] else 0.0
    refuted = per_label["REFUTED"]
    return {
        "labels": per_label,
        "wrongful_refutation_rate": refuted["matched"] / refuted["findings"] if refuted["findings"] else 0.0,
        "golden_lost_by_adjudication": lost,
    }


def select_default(
    records: list[dict],
    baseline: dict[str, tuple],
    profile: str,
    labels: str = "cp",
    n_boot: int = 2000,
) -> dict:
    """PLAN Phase 4 selection rule: the smallest subset within one bootstrap standard
    error of the best F1, with precision at least the baseline's (`/code-review`)."""
    members = members_in(records)
    rows = []
    for k in range(1, len(members) + 1):
        for subset in combinations(members, k):
            view = View(f"board:{','.join(subset)}:{labels}", "board-full", frozenset(subset), LABEL_FILTERS[labels])
            units = per_pr({(r["pr_id"], r["repeat"]): score(r, view) for r in records}, profile)
            rows.append({"members": subset, "units": units, **summarize(units)})
    best = max(rows, key=lambda r: r["f1"])
    se = bootstrap(best["units"], n=n_boot)["se"]
    shared = sorted(set(baseline) & set(best["units"]))
    floor = summarize(baseline, shared)["precision"] if shared else 0.0
    eligible = [r for r in rows if r["f1"] >= best["f1"] - se and r["precision"] >= floor]
    chosen = min(eligible, key=lambda r: (len(r["members"]), -r["f1"])) if eligible else None
    rows.sort(key=lambda r: -r["f1"])
    return {"best": best, "se": se, "precision_floor": floor, "chosen": chosen, "rows": rows}
