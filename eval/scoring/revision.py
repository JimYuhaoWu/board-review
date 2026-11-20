"""PLAN v3, R0 (TASKS J10): scoring the reviser's runs (Y15) against Qodo's injection commit.

Per run, from `revision.diff`, `vs-clean.diff`, `report.json` and the account-A key file:

- **Planted bugs** (Core: category `bug`), each decided in this order:
  1. `reverted`: the bug has injected blocks and all of them are back to `head^`'s text. Fixed.
  2. `untouched`: the revision changes no file the bug is in (no file at all, for a bug with no
     location). Not fixed.
  3. `judged`: the fix judge decides, from the bug, its injected blocks and the revision's edits
     in those files (all edits, for a bug with no location).
- **Wrong edits**: revision blocks not within 3 lines of an injected block (`hunks.on_target`).
- **Rule issues** (All profile only): reverted or not; no judge.
- **Findings**: each finding's label from the key and the reviser's action.

The fix judge is the review judge's model on the local endpoint, with FIX_PROMPT. Jim's blind check
of it (PLAN v3, 6) samples judged bugs with `sample_check` and scores his answers with `check_agreement`.
"""

from __future__ import annotations

import csv
import json
import random
from pathlib import Path

from . import hunks
from .metrics import bootstrap, summarize
from .pipeline import RESULTS, judge_slug
from .revise import keys_dir

INPUTS = ("N1", "C5", "G")
EDIT_LINES = 400  # the most revision-diff lines shown to the fix judge
FIX_PROMPT = """A bug was planted in a pull request. Afterwards, a reviser edited the pull request. \
Decide whether the reviser's edits fix the planted bug.

The planted bug:
{bug}

Where it was planted. Lines starting with "-" are the code before the bug was planted, lines \
starting with "+" the code that planted it:
{planted}

The reviser's edits. Lines starting with "-" were removed, lines starting with "+" added:
{edits}

The bug is fixed if, after the edits, the problem described above no longer occurs. The fix does \
not have to restore the original code. A partial fix, or one that moves the same problem elsewhere, \
is not a fix.

Answer with JSON only: {{"fixed": true or false, "reasoning": "<one sentence>"}}"""


def run_dir(dataset: str, input_: str, pr_id: str, repeat: int) -> Path:
    return RESULTS / dataset / f"revise-{input_}" / pr_id / f"r{repeat}"


def scored_path(dataset: str, judge: str, input_: str, pr_id: str, repeat: int) -> Path:
    return RESULTS / dataset / "judged" / judge_slug(judge) / f"revise-{input_}" / pr_id / f"r{repeat}.json"


def render(blocks: list[hunks.Block], side: str = "new") -> str:
    """Blocks as the judge reads them: the file and line, then the removed and added lines."""
    if not blocks:
        return "(none)"
    out = []
    for b in blocks:
        out.append(f"{b.path}, line {getattr(b, side)[0]}:")
        out += [f"-{line}" for line in b.removed] + [f"+{line}" for line in b.added]
    return "\n".join(out)


def _cap(text: str) -> str:
    lines = text.splitlines()
    if len(lines) <= EDIT_LINES:
        return text
    return "\n".join(lines[:EDIT_LINES] + [f"... ({len(lines) - EDIT_LINES} more lines not shown)"])


def bug_questions(golden: list[dict], injection: list[hunks.Block], revision: list[hunks.Block],
                  vs_clean: list[hunks.Block], category: str = "bug") -> list[dict]:
    """Each planted bug of `category` with its decision, or the fix-judge prompt when it needs one."""
    out = []
    for gi, g in enumerate(golden):
        if g["category"] != category:
            continue
        blocks = hunks.bug_blocks(g["comment"], injection)
        loc = hunks.location(g["comment"])
        files = {b.path for b in blocks} or ({loc[0]} if loc else set())
        edits = [r for r in revision if r.path in files] if files else revision
        item = {"golden": gi, "comment": g["comment"], "blocks": len(blocks)}
        if blocks and all(hunks.reverted(b, vs_clean) for b in blocks):
            item.update(status="reverted", fixed=True)
        elif not edits:
            item.update(status="untouched", fixed=False)
        else:
            planted, shown = render(blocks), _cap(render(edits, "old"))
            item.update(status="judged", fixed=None, planted=planted, edits=shown,
                        prompt=FIX_PROMPT.format(bug=g["comment"], planted=planted, edits=shown))
        out.append(item)
    return out


def score_run(dataset: str, input_: str, pr_id: str, repeat: int, golden: list[dict],
              injection: list[hunks.Block]) -> dict:
    """A run's record before judging: bug decisions (prompts for the judge), edits, rules, findings."""
    d = run_dir(dataset, input_, pr_id, repeat)
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    if meta["status"] != "ok":
        return {"pr_id": pr_id, "input": input_, "repeat": repeat, "status": meta["status"]}
    revision = hunks.parse_diff((d / "revision.diff").read_text(encoding="utf-8", errors="replace"))
    vs_clean = hunks.parse_diff((d / "vs-clean.diff").read_text(encoding="utf-8", errors="replace"))
    report = json.loads((d / "report.json").read_text(encoding="utf-8"))
    key = json.loads((keys_dir(dataset) / pr_id / f"{input_}-r{repeat}.json").read_text(encoding="utf-8"))
    on = [hunks.on_target(b, injection) for b in revision]
    rules = [{"golden": q["golden"], "reverted": q["status"] == "reverted"}
             for q in bug_questions(golden, injection, revision, vs_clean, "rule") if q["blocks"]]
    return {
        "pr_id": pr_id, "input": input_, "repeat": repeat, "status": "ok",
        "cost_usd": meta.get("total_cost_usd"), "prompt_sha256": (meta.get("revise") or {}).get("prompt_sha256"),
        "bugs": bug_questions(golden, injection, revision, vs_clean),
        "edits": {"blocks": len(revision), "on_target": sum(on), "wrong": len(on) - sum(on),
                  "lines": sum(len(b.removed) + len(b.added) for b in revision)},
        "rules": rules,
        "findings": [{"id": e["id"], "label": key["findings"][e["id"]]["label"], "action": e["action"]}
                     for e in report["findings"]],
    }


# --- aggregation -------------------------------------------------------------------------------

def per_pr(records: list[dict]) -> dict[str, tuple[float, float, float]]:
    """{pr_id: (bugs fixed, wrong edits, bugs not fixed)}, averaged over repeats: the `Counts` shape,
    so `metrics.bootstrap` on `fp_per_pr` gives wrong edits per PR and on `tp` the bugs fixed."""
    by: dict[str, list[tuple[int, int, int]]] = {}
    for r in records:
        if r["status"] != "ok":
            continue
        fixed = sum(1 for b in r["bugs"] if b["fixed"])
        by.setdefault(r["pr_id"], []).append((fixed, r["edits"]["wrong"], len(r["bugs"]) - fixed))
    return {p: tuple(sum(c[i] for c in cs) / len(cs) for i in range(3)) for p, cs in by.items()}


def summary(records: list[dict]) -> dict:
    ok = [r for r in records if r["status"] == "ok"]
    units = per_pr(ok)
    s = summarize(units)
    bugs = [b for r in ok for b in r["bugs"]]
    how = {k: sum(1 for b in bugs if b["status"] == k) for k in ("reverted", "judged", "untouched")}
    table: dict[str, dict[str, int]] = {}
    for r in ok:
        for f in r["findings"]:
            table.setdefault(f["label"], {"fixed": 0, "declined": 0})[f["action"]] += 1
    return {
        "runs": len(records), "ok": len(ok), "prs": s["prs"],
        "bugs_fixed_per_pr": s["tp"] / s["prs"] if s["prs"] else 0.0,
        "fix_rate": s["recall"], "wrong_edits_per_pr": s["fp_per_pr"],
        "decided": how, "judged_fixed": sum(1 for b in bugs if b["status"] == "judged" and b["fixed"]),
        "rules_reverted_per_run": sum(x["reverted"] for r in ok for x in r["rules"]) / len(ok) if ok else 0.0,
        "lines_per_run": sum(r["edits"]["lines"] for r in ok) / len(ok) if ok else 0.0,
        "cost_per_run": sum(r["cost_usd"] or 0 for r in ok) / len(ok) if ok else 0.0,
        "findings": table,
    }


def decide(c5: list[dict], n1: list[dict], n_boot: int = 2000, seed: int = 20261120) -> dict:
    """PLAN v3, R2: wrong edits per PR, C5 − N1, paired bootstrap, 95% CI below 0; and C5's planted
    bugs fixed per PR at least 90% of N1's (point estimate)."""
    a, b = per_pr(c5), per_pr(n1)
    delta = bootstrap(a, b, "fp_per_pr", n_boot, seed)
    prs = sorted(set(a) & set(b))
    fixed_c5, fixed_n1 = (sum(u[p][0] for p in prs) / len(prs) for u in (a, b))
    ratio = fixed_c5 / fixed_n1 if fixed_n1 else 0.0
    passed = delta["ci95"][1] < 0 and ratio >= 0.9
    return {"delta_wrong_edits": delta, "fixed_c5": fixed_c5, "fixed_n1": fixed_n1, "fixed_ratio": ratio,
            "verdict": "PASS" if passed else "FAIL"}


# --- Jim's blind check of the fix judge (PLAN v3, 6) -------------------------------------------

CHECK_FIELDS = ("pair", "bug", "planted", "edits", "fixed (yes/no)")


def sample_check(records: list[dict], n: int, out: Path, seed: int = 20261120) -> int:
    """Write n judged bugs, without the judge's answers, as a CSV for Jim, and a key CSV beside it
    (`<out>.key.csv`) that he doesn't open."""
    pairs = [(r, b) for r in records if r["status"] == "ok" for b in r["bugs"] if b["status"] == "judged"]
    random.Random(seed).shuffle(pairs)
    pairs = pairs[:n]
    with open(out, "w", encoding="utf-8-sig", newline="") as f, open(_key(out), "w", encoding="utf-8", newline="") as k:
        w, kw = csv.writer(f), csv.writer(k)
        w.writerow(CHECK_FIELDS)
        kw.writerow(("pair", "pr_id", "input", "repeat", "golden", "judge_fixed"))
        for i, (r, b) in enumerate(pairs, 1):
            w.writerow((i, b["comment"], b["planted"], b["edits"], ""))
            kw.writerow((i, r["pr_id"], r["input"], r["repeat"], b["golden"], b["fixed"]))
    return len(pairs)


def _key(out: Path) -> Path:
    return out.with_name(out.stem + ".key.csv")


def check_agreement(answers: Path) -> dict:
    """Jim's answers against the judge's: the share of answered pairs where both say the same."""
    with open(_key(answers), encoding="utf-8") as k:
        judge = {row["pair"]: row["judge_fixed"] == "True" for row in csv.DictReader(k)}
    same = total = 0
    with open(answers, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            mine = row["fixed (yes/no)"].strip().lower()
            if mine in ("yes", "no"):
                total += 1
                same += (mine == "yes") == judge[row["pair"]]
    return {"pairs": len(judge), "answered": total, "same": same, "agreement": same / total if total else float("nan")}
