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


def _units(records: list[dict], members: frozenset[str] | None, labels: str, profile: str) -> dict:
    name = ",".join(sorted(members)) if members is not None else "full"
    view = View(f"board:{name}:{labels}", "board-full", members, LABEL_FILTERS[labels])
    return per_pr({(r["pr_id"], r["repeat"]): score(r, view) for r in records}, profile)


def member_cards(
    records: list[dict],
    baseline: dict[tuple[str, int], dict],
    categories: dict[str, list[str]],
    usage: dict[str, float],
    profile: str,
    labels: str = "cp",
    n_boot: int = 2000,
) -> list[dict]:
    """PLAN 6.5, one card per member, over the given board runs:

    - runs: how many runs the member ran in (triggers skip some);
    - own categories: golden comments in the categories its brief targets that it found,
      next to what `/code-review` (baseline) found on the same PRs, and the total;
    - contribution: board F1 minus the F1 of the board without it, with a paired bootstrap
      over PRs (positive: the member helps);
    - usage share: its share of the price-weighted usage of all members and verifiers.
    """
    allowed = LABEL_FILTERS[labels]
    members = members_in(records)
    full = _units(records, None, labels, profile)
    base = defaultdict(lambda: defaultdict(list))  # pr -> category -> [(found, total)] per repeat
    for (pid, _), res in baseline.items():
        if res.get("skipped"):
            continue
        c = defaultdict(lambda: [0, 0])
        for t in res["true_positives"]:
            c[t.get("category")][0] += 1
        for t in res["true_positives"] + res["false_negatives"]:
            c[t.get("category")][1] += 1
        for cat, v in c.items():
            base[pid][cat].append(v)
    prs = {r["pr_id"] for r in records}
    total_usage = sum(usage.values())
    cards = []
    for member in members:
        cats = set(categories.get(member, []))
        runs = sum(member in r.get("members", {}).get("ran", []) or any(c["member"] == member for c in r["candidates"])
                   for r in records)
        found = defaultdict(list)  # pr -> golden comments in its categories found, per run
        for r in records:
            hits = set()
            for c, m in zip(r["candidates"], _matches(r, "all"), strict=True):
                if c["member"] == member and (allowed is None or c["label"] in allowed):
                    hits |= {gi for gi in m if r["golden"][gi].get("category") in cats}
            found[r["pr_id"]].append(len(hits))
        own = sum(sum(v) / len(v) for v in found.values())
        cr_found = sum(sum(f for f, _ in base[pid][cat]) / len(base[pid][cat])
                       for pid in prs for cat in cats if base[pid][cat])
        own_total = sum(
            sum(1 for g in r["golden"] if g.get("category") in cats) for r in {r["pr_id"]: r for r in records}.values()
        )
        without = _units(records, frozenset(m for m in members if m != member), labels, profile)
        b = bootstrap(full, without, n=n_boot)
        cards.append({
            "member": member, "runs": runs, "categories": sorted(cats),
            "own_found": own, "own_found_baseline": cr_found, "own_total": own_total,
            "f1_without": summarize(without)["f1"], "contribution": b["point"], "ci95": b["ci95"],
            "usage_share": usage.get(member, 0.0) / total_usage if total_usage else 0.0,
        })
    return cards


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
