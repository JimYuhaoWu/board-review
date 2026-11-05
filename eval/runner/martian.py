"""Martian Code Review Bench adapter (TASKS Y1 and Y2).

Reads the golden comments at the pinned commit (eval/datasets/martian/SOURCE.md), looks up each
PR on GitHub, and builds one history-limited repository per PR (see gitrepo).

The tools on Martian's leaderboard reviewed GitHub's PR diff, which starts at the merge-base of
the PR's base commit and its head. GitHub's `base.sha` is not always that merge-base: when the
base branch moved on, `base.sha..head` would add the reversed later changes. So `base` in
prs.jsonl is the merge-base, and `<base>..<head>` is the reviewed diff.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from eval.runner import gitrepo
from eval.runner.records import GoldenComment, PullRequest

PINNED_COMMIT = "e616e849755441da38f18bf3adba2c9583b03803"
PROJECTS = ("sentry", "grafana", "keycloak", "discourse", "cal_dot_com")
_GOLDEN_URL = (
    "https://raw.githubusercontent.com/withmartian/code-review-benchmark/"
    "{commit}/offline/golden_comments/{project}.json"
)
_PR_URL = re.compile(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)")


def parse_pr_url(url: str) -> tuple[str, str, int]:
    match = _PR_URL.search(url)
    if not match:
        raise ValueError(f"not a GitHub pull request URL: {url}")
    owner, repo, number = match.groups()
    return owner, repo.removesuffix(".git"), int(number)


class MartianComment(BaseModel):
    comment: str
    severity: str
    category: str


class MartianEntry(BaseModel):
    """One PR in a Martian golden file."""

    project: str
    pr_title: str
    url: str
    original_url: str | None = None
    az_comment: str | None = None
    comments: list[MartianComment]

    @classmethod
    def from_martian(cls, project: str, raw: dict) -> MartianEntry:
        return cls(project=project, **raw)

    @property
    def pr_id(self) -> str:
        """`<project>-<number>`, using the upstream PR number when the URL points at a fork.

        Some fork PRs were built from a single upstream commit, so `original_url` is a commit
        URL; those keep the fork's PR number.
        """
        source = self.original_url if self.original_url and _PR_URL.search(self.original_url) else self.url
        _, _, number = parse_pr_url(source)
        return f"{self.project}-{number}"


class GitHubPR(BaseModel):
    base_sha: str
    head_sha: str
    title: str
    body: str

    @classmethod
    def from_api(cls, data: dict) -> GitHubPR:
        return cls(
            base_sha=data["base"]["sha"],
            head_sha=data["head"]["sha"],
            title=data["title"],
            body=data.get("body") or "",
        )


def load_entries(commit: str = PINNED_COMMIT) -> list[MartianEntry]:
    entries = []
    for project in PROJECTS:
        url = _GOLDEN_URL.format(commit=commit, project=project)
        with urllib.request.urlopen(url, timeout=60) as response:
            raw = json.load(response)
        entries.extend(MartianEntry.from_martian(project, item) for item in raw)
    ids = [entry.pr_id for entry in entries]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"duplicate PR ids: {sorted(duplicates)}")
    return entries


def fetch_pr_details(url: str) -> GitHubPR:
    owner, repo, number = parse_pr_url(url)
    result = subprocess.run(
        ["gh", "api", f"repos/{owner}/{repo}/pulls/{number}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "gh api failed")
    return GitHubPR.from_api(json.loads(result.stdout))


def intent_text(title: str, body: str) -> str:
    body = body.replace("\r\n", "\n").strip()
    return f"{title.strip()}\n\n{body}" if body else title.strip()


def describe(title: str, body: str) -> tuple[str, str | None]:
    """Intent text, and the upstream PR URL if the PR names one (Martian's golden file does)."""
    return intent_text(title, body), None


def golden_records(entries: list[MartianEntry]) -> list[GoldenComment]:
    return [
        GoldenComment(pr_id=entry.pr_id, comment=c.comment, severity=c.severity, category=c.category)
        for entry in entries
        for c in entry.comments
    ]


def build_pull_requests(
    entries: list[MartianEntry],
    data_dir: Path,
    known: dict[str, PullRequest],
    details: Callable[[str], GitHubPR] = fetch_pr_details,
    describe: Callable[[str, str], tuple[str, str | None]] = describe,
) -> list[PullRequest]:
    """Look up each PR (unless `known` already records it), fetch its commits, and build its repo.

    `describe` turns the GitHub title and body into the intent text and, when the body names it,
    the upstream PR URL (the Qodo adapter uses this).
    """
    prs = []
    for n, entry in enumerate(entries, 1):
        print(f"[{n}/{len(entries)}] {entry.pr_id}", file=sys.stderr, flush=True)
        prs.append(_build_one(entry, data_dir, known.get(entry.pr_id), details, describe))
    return prs


def _build_one(
    entry: MartianEntry,
    data_dir: Path,
    known: PullRequest | None,
    details: Callable[[str], GitHubPR],
    describe: Callable[[str, str], tuple[str, str | None]] = describe,
) -> PullRequest:
    pr = PullRequest(
        id=entry.pr_id,
        repo=entry.project,
        url=entry.url,
        original_url=entry.original_url,
        intent=entry.pr_title,
        runnable=False,
    )
    if entry.az_comment:
        return pr.model_copy(update={"reason": f"Martian: {entry.az_comment}"})

    if known and known.head and known.github_base:
        github_base, head, intent = known.github_base, known.head, known.intent
        original_url = entry.original_url or known.original_url
    else:
        try:
            found = details(entry.url)
        except RuntimeError as e:
            return pr.model_copy(update={"reason": f"GitHub lookup failed: {e}"})
        github_base, head = found.base_sha, found.head_sha
        intent, named = describe(found.title, found.body)
        original_url = entry.original_url or named
    pr = pr.model_copy(
        update={"github_base": github_base, "head": head, "intent": intent, "original_url": original_url}
    )

    cache = data_dir / "cache" / f"{entry.project}.git"
    gitrepo.ensure_bare(cache)
    owner, repo, number = parse_pr_url(entry.url)
    remote = f"https://github.com/{owner}/{repo}.git"
    ref = f"refs/board-eval/{entry.pr_id}"

    if not gitrepo.fetch(cache, remote, f"+pull/{number}/head:{ref}/head"):
        return pr.model_copy(update={"reason": "PR head not fetchable"})
    fetched = gitrepo.rev_parse(cache, f"{ref}/head")
    if fetched != head:
        return pr.model_copy(update={"reason": f"PR head is {fetched}, expected {head}"})
    if not gitrepo.has_commit(cache, github_base) and not gitrepo.fetch(cache, remote, github_base):
        return pr.model_copy(update={"reason": "base commit not fetchable"})
    gitrepo.update_ref(cache, f"{ref}/base", github_base)

    base = gitrepo.merge_base(cache, github_base, head)
    if base is None:
        return pr.model_copy(update={"reason": "no merge-base between base and head"})
    files, added, removed = gitrepo.diff_stats(cache, base, head)
    if files == 0:
        return pr.model_copy(update={"base": base, "reason": "empty diff"})

    gitrepo.build_pr_repo(cache, data_dir / "prs" / f"{entry.pr_id}.git", f"{ref}/head", head)
    return pr.model_copy(
        update={
            "base": base,
            "runnable": True,
            "files_changed": files,
            "additions": added,
            "deletions": removed,
            "agent_files": gitrepo.agent_files(cache, head),
        }
    )
