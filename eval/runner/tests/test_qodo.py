from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.runner import gitrepo, qodo
from eval.runner.martian import GitHubPR
from eval.scoring.profiles import PROFILE_CATEGORIES

_BUG = {
    "title": "Listeners never registered",
    "description": "slack.listen() was removed from init.",
    "file_path": "core/boot.js",
    "start_line": 40,
    "end_line": 44,
    "problematic_code_snippet": "await Promise.all([...])",
}
_RULE = {**_BUG, "title": "NPM referenced instead of Yarn.", "start_line": 313, "end_line": 313,
         "rule_name": "Package Manager Must Be Yarn v1"}


def _line(repo: str = "cal.com", number: int = 3, issues: list[dict] | None = None) -> str:
    issues = [_BUG, _RULE] if issues is None else issues
    return json.dumps({
        "repo": repo,
        "pr_url_to_review": f"https://github.com/agentic-review-benchmarks/{repo}/pull/{number}",
        "num_of_issues": len(issues),
        "issues": issues,
    })


def test_pr_id_uses_a_safe_repo_name_and_the_fork_number():
    [entry] = qodo.parse_entries([_line("cal.com", 3)])
    assert entry.pr_id == "cal_com-3"
    [entry] = qodo.parse_entries([_line("firefox-ios", 12)])
    assert entry.pr_id == "firefox_ios-12"


def test_functional_issues_are_bugs_and_rule_violations_are_rules():
    records = qodo.golden_records(qodo.parse_entries([_line()]))
    assert [r.category for r in records] == ["bug", "rule"]
    assert {r.severity for r in records} == {"Unrated"}
    assert records[0].comment == "Listeners never registered. slack.listen() was removed from init. (core/boot.js:40-44)"
    assert records[1].comment.endswith("(core/boot.js:313) Repository rule: Package Manager Must Be Yarn v1.")


def test_issue_without_a_location_has_no_location_suffix():
    issue = {**_BUG, "file_path": None, "start_line": None, "end_line": None}
    [record] = qodo.golden_records(qodo.parse_entries([_line(issues=[issue])]))
    assert record.comment == "Listeners never registered. slack.listen() was removed from init."


def test_core_counts_functional_bugs_and_all_counts_rules_too():
    assert "bug" in PROFILE_CATEGORIES["core"] and "rule" not in PROFILE_CATEGORIES["core"]
    assert "rule" in PROFILE_CATEGORIES["all"]


def test_issue_count_mismatch_is_refused():
    bad = json.loads(_line())
    bad["num_of_issues"] = 3
    with pytest.raises(ValueError, match="num_of_issues"):
        qodo.parse_entries([json.dumps(bad)])


def test_duplicate_ids_are_refused():
    with pytest.raises(ValueError, match="duplicate"):
        qodo.parse_entries([_line(), _line()])


_BODY = (
    "## Code Review Bench PR #25799\r\n\r\n"
    "**Original PR Title**: Removed xmlrpc ping service\n"
    "**Original PR Description**: Pingomatic is a legacy service.\r\n\r\n- Why?\r\n"
    "**Original PR URL**: https://github.com/TryGhost/Ghost/pull/25799\n\n"
)


def test_describe_keeps_only_the_upstream_title_and_description():
    intent, url = qodo.describe("Code Review Bench PR #25799 - Removed xmlrpc ping service", _BODY)
    assert intent == "Removed xmlrpc ping service\n\nPingomatic is a legacy service.\n\n- Why?"
    assert "Code Review Bench" not in intent
    assert url == "https://github.com/TryGhost/Ghost/pull/25799"


def test_describe_falls_back_to_the_title_without_its_prefix():
    intent, url = qodo.describe("Code Review Bench PR #7 - Fix paging", "")
    assert (intent, url) == ("Fix paging", None)


def test_build_records_the_upstream_url_from_the_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def details(url: str) -> GitHubPR:
        return GitHubPR(base_sha="b" * 40, head_sha="h" * 40, title="Code Review Bench PR #1 - X", body=_BODY)

    monkeypatch.setattr(gitrepo, "fetch", lambda *args, **kwargs: False)
    [pr] = qodo.build_pull_requests(qodo.parse_entries([_line("Ghost", 1)]), tmp_path, {}, details)
    assert pr.id == "ghost-1"
    assert pr.original_url == "https://github.com/TryGhost/Ghost/pull/25799"
    assert pr.intent.startswith("Removed xmlrpc ping service")
    assert not pr.runnable and pr.reason == "PR head not fetchable"
