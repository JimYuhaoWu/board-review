from __future__ import annotations

from pathlib import Path

import pytest

from eval.runner.records import PullRequest
from eval.runner.split import make_split, write_frozen


def _prs() -> list[PullRequest]:
    prs = [
        PullRequest(id=f"{repo}-{n}", repo=repo, url="u", runnable=True)
        for repo in ("alpha", "beta")
        for n in range(1, 9)
    ]
    prs.append(PullRequest(id="alpha-99", repo="alpha", url="u", runnable=False, reason="unusable"))
    return prs


def test_split_is_stratified_and_complete():
    split = make_split(_prs(), seed=1)
    assert len(split.dev) == 6 and len(split.test) == 10
    assert sorted(split.dev + split.test) == sorted(p.id for p in _prs() if p.runnable)
    for repo in ("alpha", "beta"):
        assert sum(i.startswith(repo) for i in split.dev) == 3
        assert sum(i.startswith(repo) for i in split.pilot) == 2
    assert set(split.pilot) <= set(split.dev)


def test_split_is_reproducible_and_ignores_input_order():
    assert make_split(_prs(), seed=7) == make_split(list(reversed(_prs())), seed=7)
    assert make_split(_prs(), seed=7) != make_split(_prs(), seed=8)


def test_frozen_split_is_never_replaced(tmp_path: Path):
    path = tmp_path / "split.json"
    write_frozen(path, make_split(_prs()))
    with pytest.raises(FileExistsError):
        write_frozen(path, make_split(_prs(), seed=2))
