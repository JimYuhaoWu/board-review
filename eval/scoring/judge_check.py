"""Jim's blind check of the review judge (PLAN v1 6; PLAN v2, Phase C): about 60 of the judge's
match / no-match verdicts, labeled by Jim without seeing them, stratified by verdict.

Half the pairs are match verdicts. The other half are no-match verdicts where the finding is in the
golden comment's file: a finding from elsewhere in the PR is trivially a different issue, so drawing
those would inflate the agreement. `sample` writes the sheet and a key beside it (`<stem>.key.csv`),
which Jim doesn't open; `agreement` scores his answers against the key.
"""

from __future__ import annotations

import csv
import random
from pathlib import Path

from .hunks import location

FIELDS = ("pair", "golden comment", "finding", "same issue (yes/no)")
SEED = 20261122


def candidates(records: list[dict]) -> tuple[list[tuple], list[tuple]]:
    """(match verdicts, same-file no-match verdicts), each as (pr_id, repeat, golden, candidate,
    golden text, finding text, match), in the order of `records`."""
    match, near = [], []
    for r in records:
        for gi, row in enumerate(r["verdicts"]):
            comment = r["golden"][gi]["comment"]
            loc = location(comment)
            for ci, v in enumerate(row):
                text = r["candidates"][ci]["text"]
                item = (r["pr_id"], r["repeat"], gi, ci, comment, text, v["match"])
                if v["match"]:
                    match.append(item)
                elif loc and text.lstrip("`").startswith(loc[0]):
                    near.append(item)
    return match, near


def _key(out: Path) -> Path:
    return out.with_name(out.stem + ".key.csv")


def sample(records: list[dict], n: int, out: Path, seed: int = SEED) -> tuple[int, int, int]:
    """Write n pairs (n/2 per verdict) for Jim, and the key. Returns (pairs, match pool, no-match pool)."""
    match, near = candidates(records)
    rng = random.Random(seed)
    pairs = rng.sample(match, n // 2) + rng.sample(near, n - n // 2)
    rng.shuffle(pairs)
    with open(out, "w", encoding="utf-8-sig", newline="") as f, open(_key(out), "w", encoding="utf-8", newline="") as k:
        w, kw = csv.writer(f), csv.writer(k)
        w.writerow(FIELDS)
        kw.writerow(("pair", "pr_id", "repeat", "golden", "candidate", "judge_match"))
        for i, (pid, rep, gi, ci, comment, text, m) in enumerate(pairs, 1):
            w.writerow((i, comment, text, ""))
            kw.writerow((i, pid, rep, gi, ci, m))
    return len(pairs), len(match), len(near)


def agreement(answers: Path) -> dict:
    """Jim's answers against the judge's verdicts, with the 2 × 2 table and the pairs that differ."""
    with open(_key(answers), encoding="utf-8") as k:
        judge = {row["pair"]: row["judge_match"] == "True" for row in csv.DictReader(k)}
    table = {(j, a): 0 for j in (True, False) for a in (True, False)}
    differ = []
    with open(answers, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            mine = row[FIELDS[-1]].strip().lower()
            if mine not in ("yes", "no"):
                continue
            table[(judge[row["pair"]], mine == "yes")] += 1
            if judge[row["pair"]] != (mine == "yes"):
                differ.append(int(row["pair"]))
    answered = sum(table.values())
    same = table[(True, True)] + table[(False, False)]
    return {"pairs": len(judge), "answered": answered, "same": same,
            "agreement": same / answered if answered else float("nan"), "table": table, "differ": sorted(differ)}
