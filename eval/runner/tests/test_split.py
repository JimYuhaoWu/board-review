from __future__ import annotations

from pathlib import Path

import pytest

from eval.runner import __main__ as cli
from eval.runner.records import PullRequest, write_jsonl
from eval.runner.split import Draw, make_draw, make_split, write_frozen


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


def test_draw_takes_per_repo_from_the_given_ids_only():
    test = make_split(_prs(), seed=1).test
    draw = make_draw(_prs(), test, seed=5, per_repo=2, source="test")
    assert (draw.seed, draw.source, draw.per_repo) == (5, "test", 2)
    assert len(draw.prs) == 4 and set(draw.prs) <= set(test)
    for repo in ("alpha", "beta"):
        assert sum(i.startswith(repo) for i in draw.prs) == 2


def test_draw_is_reproducible_and_ignores_input_order():
    test = make_split(_prs(), seed=1).test
    draw = make_draw(_prs(), test, seed=5, per_repo=2, source="test")
    assert draw == make_draw(list(reversed(_prs())), list(reversed(test)), seed=5, per_repo=2, source="test")
    assert draw != make_draw(_prs(), test, seed=6, per_repo=2, source="test")


def test_draw_needs_enough_prs_in_each_repo():
    with pytest.raises(ValueError, match="alpha has 5"):
        make_draw(_prs(), make_split(_prs(), seed=1).test, seed=5, per_repo=6, source="test")


def test_draw_command_writes_the_draw_once(tmp_path: Path):
    write_jsonl(tmp_path / "prs.jsonl", _prs())
    split = make_split(_prs(), seed=1)
    write_frozen(tmp_path / "split.json", split)
    args = ["draw", "--dataset-dir", str(tmp_path), "--name", "gate3", "--per-repo", "2", "--seed", "5"]
    assert cli.main(args) == 0
    written = Draw.model_validate_json((tmp_path / "gate3.json").read_text(encoding="utf-8"))
    assert written == make_draw(_prs(), split.test, seed=5, per_repo=2, source="test")
    with pytest.raises(FileExistsError):
        cli.main([*args[:-1], "6"])


def test_draw_command_leaves_out_earlier_draws(tmp_path: Path):
    write_jsonl(tmp_path / "prs.jsonl", _prs())
    split = make_split(_prs(), seed=1)
    write_frozen(tmp_path / "split.json", split)
    base = ["draw", "--dataset-dir", str(tmp_path), "--seed", "5"]
    assert cli.main([*base, "--name", "gate3", "--per-repo", "2"]) == 0
    assert cli.main([*base, "--name", "gate4", "--per-repo", "3"]) == 0
    gate3 = Draw.model_validate_json((tmp_path / "gate3.json").read_text(encoding="utf-8"))
    gate4 = Draw.model_validate_json((tmp_path / "gate4.json").read_text(encoding="utf-8"))
    assert not set(gate3.prs) & set(gate4.prs)
    rest = [i for i in split.test if i not in gate3.prs]
    assert gate4 == make_draw(_prs(), rest, seed=5, per_repo=3, source="test")
    with pytest.raises(ValueError, match="alpha has 0"):
        cli.main([*base, "--name", "gate5", "--per-repo", "1"])
