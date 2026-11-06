"""Turn finished runs into judged records: extract, dedup, and judge every candidate once.

A judged record holds everything later scoring needs, so any view (roster, label filter)
is scored offline from it without new judge calls (PLAN 6.2):

- candidates: extracted issue texts, each tagged with the finding, member, and label it came from;
- groups: Martian dedup groups over all candidates of the run;
- verdicts: the judge's answer for every (golden comment, candidate) pair.

Both variants report structured findings (the board's JSON, `/code-review`'s ReportFindings
call). Each finding is one candidate, rendered as its location and statement, so every
candidate keeps its member and label. Mode `whole` instead runs Martian's extraction on the
whole report (one item per board group, CONFIRMED + PLAUSIBLE), the way Martian treats a
tool's review, as a check.
"""

import asyncio
import hashlib
import json
from pathlib import Path

from .data import RESULTS, Run, parse_board_output, render_finding, representatives, review_findings
from .llm import JudgeModel

SCHEMA_VERSION = 1
WHOLE = "@whole"  # suffix for records of a variant extracted as one comment


def judge_slug(model: str) -> str:
    return model.strip().replace("/", "_").replace(":", "_")


def record_path(dataset: str, judge: str, variant: str, pr_id: str, repeat: int) -> Path:
    return RESULTS / dataset / "judged" / judge_slug(judge) / variant / pr_id / f"r{repeat}.json"


def _source_sha(run: Run) -> str:
    h = hashlib.sha256()
    for name in ("final.json", "final.md", "stream.jsonl"):
        if (run.path / name).exists():
            h.update((run.path / name).read_bytes())
    return h.hexdigest()


async def _candidates(run: Run, findings: list[dict] | None, jm: JudgeModel, whole: bool) -> list[dict]:
    def bare(texts):
        return [{"text": t, "finding": None, "member": None, "label": None} for t in texts]

    if findings is None:  # `/code-review` without structured findings: its reply, whole
        return bare(await jm.extract((run.path / "final.md").read_text(encoding="utf-8")))
    if whole:
        kept = representatives([f for f in findings if f["label"] in (None, "CONFIRMED", "PLAUSIBLE")])
        return bare(await jm.extract("\n\n---\n\n".join(render_finding(f) for f in kept)))  # Martian's separator

    # Each structured finding is one issue already, so it is one candidate as it stands. Extracting
    # findings one at a time split compound sentences into several candidates (smoke test: one
    # "four behaviors are untested" finding became five), while extracting the whole report kept
    # one candidate per item. Martian too scores raw comments when nothing was extracted.
    return [{"text": render_finding(f), "finding": f["id"], "member": f["member"], "label": f["label"]}
            for f in findings]


async def judge_run(run: Run, golden: list[dict], jm: JudgeModel, whole: bool = False, force: bool = False) -> Path:
    variant = run.variant + WHOLE if whole else run.variant
    out = record_path(run.dataset, jm.model, variant, run.pr_id, run.repeat)
    source_sha = _source_sha(run)
    if out.exists() and not force:
        old = json.loads(out.read_text(encoding="utf-8"))
        # A dedup fallback is usually a passing endpoint error, so retry it; other calls come from the cache.
        if (old["source_sha256"] == source_sha and old["judge"]["fingerprint"] == jm.fingerprint
                and not old["dedup_fallback"]):
            return out

    findings = review_findings(run)
    candidates = await _candidates(run, findings, jm, whole)
    texts = [c["text"] for c in candidates]
    groups, fell_back = await jm.dedup(texts)
    pairs = [(g, c) for g in golden for c in texts]
    answers = await asyncio.gather(*(jm.match(g["comment"], c) for g, c in pairs))
    verdicts = [
        [
            {k: a.get(k) for k in ("match", "confidence", "reasoning")}
            for a in answers[i * len(texts) : (i + 1) * len(texts)]
        ]
        for i in range(len(golden))
    ]

    record = {
        "schema_version": SCHEMA_VERSION,
        "dataset": run.dataset,
        "variant": variant,
        "pr_id": run.pr_id,
        "repeat": run.repeat,
        "source_sha256": source_sha,
        "judge": {"model": jm.model, "fingerprint": jm.fingerprint, "served": jm.served},
        "golden": golden,
        "candidates": candidates,
        "groups": groups,
        "dedup_fallback": fell_back,
        "verdicts": verdicts,
        "run_meta": run.meta,
    }
    if findings is not None:
        record["findings"] = [{k: f[k] for k in ("id", "member", "label", "group", "file", "line", "text")} for f in findings]
    if run.variant == "board-full":
        record["members"] = parse_board_output((run.path / "final.json").read_text(encoding="utf-8")).get("members", {})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


MERGED = "code-review-x2"  # two `/code-review` runs merged into one review (PLAN Gate 3, variant A1)


async def merge_records(records: list[dict], jm: JudgeModel, repeat: int) -> Path:
    """Merge judged `/code-review` records of one PR into one review, variant `code-review-x2`.

    Candidates and verdicts are concatenated, so no match call is new. Finding ids get the source
    repeat as a prefix (`r1/cr-1`). Martian's strict dedup step then runs once over the merged
    candidates, and the view `code-review-x2` counts each dedup group once, a hit if any of its
    findings matches: the same rule as `board:full:cp:issues` (PLAN Gate 3). No finding is chosen
    over another, so nothing that either run found is lost by the merge.
    """
    first = records[0]
    if len(records) < 2 or any(r["variant"] != "code-review" for r in records):
        raise ValueError("merge needs two or more code-review records")
    if any((r["dataset"], r["pr_id"], r["golden"]) != (first["dataset"], first["pr_id"], first["golden"]) for r in records):
        raise ValueError("records to merge must be of one PR, with the same golden comments")
    if len({r["judge"]["fingerprint"] for r in records}) > 1:
        raise ValueError("records to merge were judged by different served models")

    candidates, findings = [], []
    for r in records:
        prefix = f"r{r['repeat']}/"
        findings += [{**f, "id": prefix + f["id"], "group": None} for f in r.get("findings", [])]
        for i, c in enumerate(r["candidates"]):
            fid = prefix + (c["finding"] or f"x{i}")
            if not c["finding"]:  # a reply without structured findings: each extracted issue stands as one
                findings.append({"id": fid, "member": None, "label": None, "group": None,
                                 "file": None, "line": None, "text": c["text"]})
            candidates.append({**c, "finding": fid})
    verdicts = [sum((r["verdicts"][g] for r in records), []) for g in range(len(first["golden"]))]

    groups, fell_back = await jm.dedup([c["text"] for c in candidates])

    record = {
        "schema_version": SCHEMA_VERSION,
        "dataset": first["dataset"],
        "variant": MERGED,
        "pr_id": first["pr_id"],
        "repeat": repeat,
        "merged_from": [r["repeat"] for r in records],
        "source_sha256": hashlib.sha256("".join(r["source_sha256"] for r in records).encode()).hexdigest(),
        "judge": first["judge"],
        "golden": first["golden"],
        "candidates": candidates,
        "groups": groups,
        "dedup_fallback": fell_back,
        "verdicts": verdicts,
        "run_meta": [r["run_meta"] for r in records],
        "findings": findings,
    }
    out = record_path(first["dataset"], first["judge"]["model"], MERGED, first["pr_id"], repeat)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def load_records(dataset: str, judge: str, variant: str, pr_ids: list[str]) -> list[dict]:
    records = []
    for pid in pr_ids:
        d = RESULTS / dataset / "judged" / judge_slug(judge) / variant / pid
        for f in sorted(d.glob("r*.json")):
            records.append(json.loads(f.read_text(encoding="utf-8")))
    return records
