"""Score a judged record under a view, with Martian's own evaluate_review.

A view picks which candidates count: a variant, optionally a roster of members, and a
label filter. Verdicts come from the record, so scoring makes no judge calls.
Dedup groups were computed once on all candidates of the run and are restricted to the
view's candidates (PLAN 6.2).
"""

import asyncio
import statistics
from dataclasses import dataclass

from .data import default_roster, representatives
from .martian import step3_judge_comments as m3
from .pipeline import WHOLE

LABEL_FILTERS = {
    "confirmed": frozenset({"CONFIRMED"}),
    "cp": frozenset({"CONFIRMED", "PLAUSIBLE"}),
    "raw": None,  # every finding, before adjudication
}


@dataclass(frozen=True)
class View:
    name: str
    variant: str
    members: frozenset[str] | None = None
    labels: frozenset[str] | None = None


def parse_view(spec: str) -> View:
    """`code-review`, `code-review@whole`, `board-full@whole`, or
    `board:<full|default|id,id>:<confirmed|cp|raw>`."""
    if spec in ("code-review", "code-review" + WHOLE, "board-full" + WHOLE):
        return View(spec, spec)
    parts = spec.split(":")
    if parts[0] != "board" or len(parts) != 3 or parts[2] not in LABEL_FILTERS:
        raise ValueError(f"bad view {spec!r}; use code-review, code-review@whole, board-full@whole, or board:<roster>:<confirmed|cp|raw>")
    roster = parts[1]
    if roster == "full":
        members = None
    elif roster == "default":
        members = frozenset(default_roster())
    else:
        members = frozenset(roster.split(","))
    return View(spec, "board-full", members, LABEL_FILTERS[parts[2]])


def select(record: dict, view: View) -> tuple[list[int], list[list[int]] | None]:
    """Indices of the view's candidates, and the run's dedup groups restricted to them.

    For the board, only each group's representative finding counts: the first finding of
    the group among the view's members, as the board's report shows one item per group.
    """
    in_view = [
        f for f in record.get("findings", [])
        if (view.members is None or f["member"] in view.members) and (view.labels is None or f["label"] in view.labels)
    ]
    kept = {f["id"] for f in representatives(in_view)}
    idx = [
        i
        for i, c in enumerate(record["candidates"])
        if (view.members is None or c["member"] in view.members)
        and (view.labels is None or c["label"] in view.labels)
        and (c["finding"] is None or c["finding"] in kept)
    ]
    groups = record["groups"]
    if groups is None:
        return idx, None
    pos = {old: new for new, old in enumerate(idx)}
    restricted = [[pos[i] for i in g if i in pos] for g in groups]
    return idx, [g for g in restricted if g]


class _RecordedJudge:
    """Stands in for Martian's LLMJudge: answers from the record's verdicts."""

    def __init__(self, record: dict):
        texts = [c["text"] for c in record["candidates"]]
        self.answers = {
            (g["comment"], texts[ci]): v
            for g, row in zip(record["golden"], record["verdicts"], strict=True)
            for ci, v in enumerate(row)
        }

    async def match_comment(self, golden_comment: str, candidate: str) -> dict:
        return self.answers[(golden_comment, candidate)]


async def _all_at_once(tasks: list, batch_size: int = 0) -> list:
    return await asyncio.gather(*tasks, return_exceptions=True)


# Martian paces its API calls with a 0.5 s pause between batches. Scoring from recorded
# verdicts makes no API calls, so the pause is dropped; the matching logic is untouched.
m3.process_batch = _all_at_once
_loop = asyncio.new_event_loop()


def score(record: dict, view: View) -> dict:
    """Martian's evaluate_review result for the view's candidates."""
    idx, groups = select(record, view)
    texts = [record["candidates"][i]["text"] for i in idx]
    return _loop.run_until_complete(
        m3.evaluate_review(_RecordedJudge(record), record["golden"], texts, groups)
    )


def panel(results: list[dict]) -> dict:
    """Combine one review's results from several judges: a golden comment is found when a
    majority of judges matched it; false positives are the median judges' count."""
    need = len(results) // 2 + 1
    found: dict[str, int] = {}
    info: dict[str, dict] = {}
    for r in results:
        for t in r["true_positives"]:
            found[t["golden_comment"]] = found.get(t["golden_comment"], 0) + 1
        for t in r["true_positives"] + r["false_negatives"]:
            info[t["golden_comment"]] = {"severity": t.get("severity"), "category": t.get("category")}
    tps = [{"golden_comment": g, **info[g]} for g in info if found.get(g, 0) >= need]
    fns = [{"golden_comment": g, **info[g]} for g in info if found.get(g, 0) < need]
    return {
        "skipped": False,
        "true_positives": tps,
        "false_negatives": fns,
        "fp": statistics.median(r["fp"] for r in results),
        "tp": len(tps),
        "fn": len(fns),
    }
