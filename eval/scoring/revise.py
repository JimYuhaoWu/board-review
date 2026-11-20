"""PLAN v3, R0 (TASKS J10): the reviser's findings files, and the answer key kept on account A.

For each PR and revision repeat k there are three inputs (PLAN v3, 5):

- N1: native `/code-review` run k's findings, from the consensus record's candidates.
- C5: the issues all of the record's runs found (the frozen add-on), each with every wording of
  it, deduplicated; the same issues for every k.
- G: the PR's golden comments in the profile (Core: functional bugs), the oracle input.

A findings file carries neutral ids (F1, F2, ...) and texts only. Which golden comments a finding
matched, and its label, are in the key file, which never leaves account A, so the reviser and account B can't see it.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from . import consensus
from .pipeline import RESULTS
from .profiles import PROFILE_CATEGORIES

INPUTS = ("N1", "C5", "G")
SCHEMA_VERSION = 1


def _matched(record: dict, ci: int) -> list[int]:
    """The golden comments, of any category, that the judge matched with candidate ci."""
    return [gi for gi, row in enumerate(record["verdicts"]) if row[ci]["match"]]


def label(golden: list[int], categories: list[str], cats: frozenset[str]) -> str:
    """`true`: matches a golden comment in the profile; `other`: matches only ones outside it (Qodo's
    rule issues under Core), which the scoring counts as neither a hit nor a false positive; `false`:
    matches none, a false positive."""
    if any(categories[g] in cats for g in golden):
        return "true"
    return "other" if golden else "false"


def _unique(texts: list[str]) -> list[str]:
    return list(dict.fromkeys(texts))


def build(record: dict, repeat: int, profile: str = "core") -> dict[str, tuple[dict, dict]]:
    """{input: (findings file, key file)} for one PR and revision repeat, from its k-run consensus record."""
    cats = PROFILE_CATEGORIES[profile]
    runs = record["merged_from"]
    if repeat not in runs:
        raise ValueError(f"{record['pr_id']}: no run {repeat} in the consensus record ({runs})")
    of = consensus.run_of(record)
    texts = [c["text"] for c in record["candidates"]]
    items: dict[str, list[tuple[list[str], list[int]]]] = {}

    items["N1"] = [([texts[i]], _matched(record, i)) for i in range(len(texts)) if of[i] == repeat]

    kept = consensus.kept(record, tuple(runs), len(runs))
    ktexts = [c["text"] for c in kept["candidates"]]
    items["C5"] = [(_unique([ktexts[i] for i in g]), sorted({gi for i in g for gi in _matched(kept, i)}))
                   for g in sorted(kept["groups"], key=min)]

    items["G"] = [([g["comment"]], [gi]) for gi, g in enumerate(record["golden"]) if g["category"] in cats]

    cats_of = [g["category"] for g in record["golden"]]
    golden_sha = hashlib.sha256(json.dumps(record["golden"], ensure_ascii=False).encode()).hexdigest()
    sources = {
        "N1": {"runs": [repeat], "record_sha256": record["source_sha256"]},
        "C5": {"runs": runs, "record_sha256": record["source_sha256"],
               "matching_prompt_sha256": record["matching"]["prompt_sha256"]},
        "G": {"golden_sha256": golden_sha, "profile": profile},
    }
    out = {}
    for name in INPUTS:
        head = {"schema_version": SCHEMA_VERSION, "dataset": record["dataset"], "pr_id": record["pr_id"],
                "input": name, "repeat": repeat}
        findings = {**head, "source": sources[name],
                    "findings": [{"id": f"F{j + 1}", "texts": t} for j, (t, _) in enumerate(items[name])]}
        key = {**head, "profile": profile, "golden": [g["comment"] for g in record["golden"]],
               "golden_categories": cats_of,
               "findings": {f"F{j + 1}": {"golden": m, "label": label(m, cats_of, cats)}
                            for j, (_, m) in enumerate(items[name])}}
        out[name] = (findings, key)
    return out


def inputs_dir(dataset: str) -> Path:
    return RESULTS / dataset / "revise-inputs"


def keys_dir(dataset: str) -> Path:
    return RESULTS / dataset / "revise-keys"


def file_name(name: str, repeat: int) -> str:
    return f"{name}-r{repeat}.json"


def write(dataset: str, pr_id: str, name: str, repeat: int, findings: dict, key: dict) -> None:
    for base, doc in ((inputs_dir(dataset), findings), (keys_dir(dataset), key)):
        path = base / pr_id / file_name(name, repeat)
        path.parent.mkdir(parents=True, exist_ok=True)
        # LF on every OS: account B hashes the findings file's bytes (input_sha256).
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")


def pack(dataset: str, pr_ids: list[str], zip_path: Path) -> int:
    """Zip the findings files (never the keys) of these PRs, at their repository paths."""
    root = RESULTS.parent.parent
    files = [p for pid in pr_ids for p in sorted((inputs_dir(dataset) / pid).glob("*.json"))]
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, p.relative_to(root).as_posix())
    return len(files)
