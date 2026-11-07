"""Freeze the development / test split (PLAN Phase 3 step 1, TASKS Y3).

Stratified by repo: each repo's runnable PRs are shuffled with a recorded seed, the first
`dev_per_repo` go to development, the rest to test. The pilot (Phase 3 step 3) takes the first
`pilot_per_repo` development PRs of each repo, so it is fixed before any run too.

A later gate draws its PRs from one part of the split the same way (`make_draw`, PLAN Phase 3,
Gate 3), into its own file, also written once, leaving out the PRs of every earlier draw.
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


class Draw(BaseModel):
    seed: int
    source: str
    per_repo: int
    prs: list[str]


def drawn_before(dataset_dir: Path) -> set[str]:
    """Every PR in a draw already written to the dataset folder: a gate never reuses another's PRs."""
    drawn: set[str] = set()
    for path in sorted(dataset_dir.glob("*.json")):
        if path.name != "split.json":
            drawn |= set(Draw.model_validate_json(path.read_text(encoding="utf-8")).prs)
    return drawn


def make_draw(
    prs: list[PullRequest], ids: list[str], seed: int, per_repo: int, source: str, exclude: set[str] = frozenset()
) -> Draw:
    """`per_repo` of `ids` from each repo: each repo's sorted ids shuffled with `seed`, as in the split.

    `exclude` leaves PRs out after grouping, so a repo it empties still has to supply `per_repo`.
    """
    repo_of = {pr.id: pr.repo for pr in prs}
    by_repo: dict[str, list[str]] = {}
    for pr_id in ids:
        pool = by_repo.setdefault(repo_of[pr_id], [])
        if pr_id not in exclude:
            pool.append(pr_id)

    rng = random.Random(seed)
    drawn = []
    for repo in sorted(by_repo):
        pool = sorted(by_repo[repo])
        if len(pool) < per_repo:
            raise ValueError(f"{repo} has {len(pool)} {source} PRs; the draw needs {per_repo}")
        rng.shuffle(pool)
        drawn += pool[:per_repo]
    return Draw(seed=seed, source=source, per_repo=per_repo, prs=drawn)


def write_frozen(path: Path, frozen: BaseModel) -> None:
    """Write a split or a draw once. An existing one is never replaced (PLAN 6.6)."""
    if path.exists():
        raise FileExistsError(f"{path} exists; it is frozen")
    path.write_text(frozen.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
