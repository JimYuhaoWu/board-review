"""Freeze the development / test split (PLAN Phase 3 step 1, TASKS Y3).

Stratified by repo: each repo's runnable PRs are shuffled with a recorded seed, the first
`dev_per_repo` go to development, the rest to test. The pilot (Phase 3 step 3) takes the first
`pilot_per_repo` development PRs of each repo, so it is fixed before any run too.
"""

from __future__ import annotations

import random
from pathlib import Path

from pydantic import BaseModel

from eval.runner.records import PullRequest

SEED = 20261103


class Split(BaseModel):
    seed: int
    dev_per_repo: int
    pilot_per_repo: int
    dev: list[str]
    test: list[str]
    pilot: list[str]


def make_split(prs: list[PullRequest], seed: int = SEED, dev_per_repo: int = 3, pilot_per_repo: int = 2) -> Split:
    if pilot_per_repo > dev_per_repo:
        raise ValueError("the pilot is drawn from development PRs, so pilot_per_repo <= dev_per_repo")
    by_repo: dict[str, list[str]] = {}
    for pr in prs:
        if pr.runnable:
            by_repo.setdefault(pr.repo, []).append(pr.id)

    rng = random.Random(seed)
    development, test, pilot = [], [], []
    for repo in sorted(by_repo):
        ids = sorted(by_repo[repo])
        if len(ids) <= dev_per_repo:
            raise ValueError(f"{repo} has {len(ids)} runnable PRs; it needs more than {dev_per_repo}")
        rng.shuffle(ids)
        development += ids[:dev_per_repo]
        pilot += ids[:pilot_per_repo]
        test += ids[dev_per_repo:]
    return Split(
        seed=seed,
        dev_per_repo=dev_per_repo,
        pilot_per_repo=pilot_per_repo,
        dev=development,
        test=test,
        pilot=pilot,
    )


def write_frozen(path: Path, split: Split) -> None:
    """Write the split once. An existing split is never replaced (PLAN 6.6)."""
    if path.exists():
        raise FileExistsError(f"{path} exists; the split is frozen")
    path.write_text(split.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
