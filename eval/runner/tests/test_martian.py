from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from eval.runner import gitrepo, martian
from eval.runner.martian import GitHubPR, MartianEntry


def _entry(**overrides) -> MartianEntry:
    raw = {
        "pr_title": "Add a",
        "url": "https://github.com/acme/widget/pull/7",
        "comments": [{"comment": "a.txt is wrong", "severity": "High", "category": "bug"}],
    }
    raw.update(overrides)
    return MartianEntry.from_martian("widget", raw)


def test_pr_id_prefers_upstream_number_for_fork_urls():
    entry = _entry(
        url="https://github.com/ai-code-review-evaluation/widget-graphite/pull/3",
        original_url="https://github.com/acme/widget/pull/4512",
    )
    assert entry.pr_id == "widget-4512"
    assert _entry().pr_id == "widget-7"


def test_pr_id_keeps_fork_number_when_original_is_a_commit():
    entry = _entry(
        url="https://github.com/ai-code-review-evaluation/widget-graphite/pull/3",
        original_url="https://github.com/acme/widget/commit/ffbaf8c54269df2ce510de91245760fddce09896",
    )
    assert entry.pr_id == "widget-3"


def test_intent_text_joins_title_and_body():
    assert martian.intent_text(" Fix paging ", "Pages start at 1.\r\n") == "Fix paging\n\nPages start at 1."
    assert martian.intent_text("Fix paging", "") == "Fix paging"


def test_github_pr_from_api_tolerates_null_body():
    pr = GitHubPR.from_api({"base": {"sha": "b"}, "head": {"sha": "h"}, "title": "t", "body": None})
    assert (pr.base_sha, pr.head_sha, pr.body) == ("b", "h", "")


def test_golden_records_map_every_comment_to_its_pr():
    records = martian.golden_records([_entry(), _entry(url="https://github.com/acme/widget/pull/8")])
    assert [r.pr_id for r in records] == ["widget-7", "widget-8"]
    assert records[0].severity == "High"


def test_unusable_pr_is_not_looked_up():
    def fail(url: str) -> GitHubPR:
        raise AssertionError("must not call GitHub")

    pr = martian._build_one(_entry(az_comment="reviewed commit is not in the repo"), Path("."), None, fail)
    assert not pr.runnable
    assert pr.reason == "Martian: reviewed commit is not in the repo"


def _run(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _commit(repo: Path, name: str, text: str, message: str) -> str:
    (repo / name).write_text(text, encoding="utf-8")
    _run(repo, "add", name)
    _run(repo, "commit", "-q", "-m", message)
    return _run(repo, "rev-parse", "HEAD")


@pytest.fixture
def upstream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """A fake GitHub repository where the base branch moved on after the PR branched off."""
    for var, value in {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }.items():
        monkeypatch.setenv(var, value)
    repo = tmp_path / "upstream"
    repo.mkdir()
    _run(repo, "init", "-q", "-b", "main")
    _run(repo, "config", "uploadpack.allowAnySHA1InWant", "true")
    fork_point = _commit(repo, "readme.txt", "hello\n", "init")
    _run(repo, "checkout", "-q", "-b", "feature")
    _commit(repo, "a.txt", "a\n", "add a")
    head = _commit(repo, "CLAUDE.md", "be nice\n", "add agent notes")
    _run(repo, "checkout", "-q", "main")
    moved_base = _commit(repo, "b.txt", "b\n", "unrelated change on main")
    future_fix = _commit(repo, "a.txt", "fixed\n", "the later fix")
    _run(repo, "update-ref", "refs/pull/7/head", head)

    data_dir = tmp_path / "data"
    cache = data_dir / "cache" / "widget.git"
    gitrepo.ensure_bare(cache)
    _run(cache, "config", f"url.{repo.as_uri()}.insteadOf", "https://github.com/acme/widget.git")
    return {
        "data_dir": data_dir, "fork_point": fork_point, "head": head,
        "moved_base": moved_base, "future_fix": future_fix,
    }


def test_build_uses_merge_base_and_hides_later_commits(upstream: dict[str, object]):
    details = lambda url: GitHubPR(
        base_sha=upstream["moved_base"], head_sha=upstream["head"], title="Add a", body="Adds a."
    )
    pr = martian._build_one(_entry(), upstream["data_dir"], None, details)

    assert pr.runnable, pr.reason
    assert pr.github_base == upstream["moved_base"]
    assert pr.base == upstream["fork_point"]
    assert (pr.files_changed, pr.additions, pr.deletions) == (2, 2, 0)
    assert pr.agent_files == ["CLAUDE.md"]
    assert pr.intent == "Add a\n\nAdds a."

    pr_repo = upstream["data_dir"] / "prs" / "widget-7.git"
    assert _run(pr_repo, "for-each-ref", "--format=%(refname)") == "refs/heads/pr"
    assert gitrepo.has_commit(pr_repo, upstream["fork_point"])
    assert not gitrepo.has_commit(pr_repo, upstream["moved_base"])
    assert not gitrepo.has_commit(pr_repo, upstream["future_fix"])


def test_reuses_known_prs_without_github(upstream: dict[str, object]):
    details = lambda url: GitHubPR(
        base_sha=upstream["moved_base"], head_sha=upstream["head"], title="Add a", body=""
    )
    first = martian._build_one(_entry(), upstream["data_dir"], None, details)

    def fail(url: str) -> GitHubPR:
        raise AssertionError("must not call GitHub")

    assert martian._build_one(_entry(), upstream["data_dir"], first, fail) == first


def test_head_mismatch_is_not_runnable(upstream: dict[str, object]):
    details = lambda url: GitHubPR(
        base_sha=upstream["moved_base"], head_sha=upstream["fork_point"], title="t", body=""
    )
    pr = martian._build_one(_entry(), upstream["data_dir"], None, details)
    assert not pr.runnable
    assert "expected" in pr.reason
