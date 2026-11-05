"""Qodo PR-Review-Bench adapter (PLAN 6.3).

Reads the benchmark file at the pinned revision (eval/datasets/qodo/SOURCE.md) and converts it to
the shared format. Each item is a pull request in a fork under github.com/agentic-review-benchmarks:
its base branch is the upstream PR's parent, and its head is the upstream PR's commits plus one
commit ("update pr") that injects the issues. The repositories are built with the Martian code
path, since both datasets are GitHub pull requests.

Golden issues come in two kinds, told apart by `rule_name`:
- **functional** bugs (no `rule_name`): category `bug`, counted by the Core profile;
- **rule** violations: breaches of a repository rule that Qodo mined from the codebase, such as
  "use yarn, not npm": category `rule`, counted only by the All profile. Qodo wrote the rules
  into each fork's AGENTS.md (and CLAUDE.md where one exists), so a reviewer that reads those
  files sees them (eval/datasets/qodo/SOURCE.md).

Qodo gives no severity, so every issue is `Unrated`.
"""

from __future__ import annotations

import re
import urllib.request
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from eval.runner import martian
from eval.runner.martian import GitHubPR
from eval.runner.records import GoldenComment, PullRequest

PINNED_REVISION = "a73957c450a70693a743260e5637fffc44625f16"
BENCH_FILE = "git_code_review_bench_100_w_open_prs.jsonl"
_URL = "https://huggingface.co/datasets/Qodo/PR-Review-Bench/resolve/{revision}/{file}"

_TITLE_PREFIX = re.compile(r"^Code Review Bench PR #\d+\s*-\s*")
_FIELD = re.compile(r"^\*\*Original PR (Title|Description|URL)\*\*:[ \t]*", re.M)


class QodoIssue(BaseModel):
    title: str
    description: str
    file_path: str | None = None  # null for 36 issues, with null lines too
    start_line: int | None = None
    end_line: int | None = None
    problematic_code_snippet: str | None = None
    rule_name: str | None = None

    @property
    def category(self) -> str:
        return "rule" if self.rule_name else "bug"

    def comment(self) -> str:
        """The golden comment the judge sees: what is wrong and where."""
        text = f"{self.title.strip().rstrip('.')}. {self.description.strip()}"
        if self.file_path:
            where = self.file_path
            if self.start_line:
                where += f":{self.start_line}"
                if self.end_line and self.end_line != self.start_line:
                    where += f"-{self.end_line}"
            text += f" ({where})"
        if self.rule_name:
            text += f" Repository rule: {self.rule_name.strip()}."
        return text


class QodoEntry(BaseModel):
    repo: str
    pr_url_to_review: str
    num_of_issues: int
    issues: list[QodoIssue]

    @property
    def project(self) -> str:
        """Repository name as an id part: `cal.com` -> `cal_com`, `firefox-ios` -> `firefox_ios`."""
        return re.sub(r"[^a-z0-9]+", "_", self.repo.lower()).strip("_")

    @property
    def pr_id(self) -> str:
        _, _, number = martian.parse_pr_url(self.pr_url_to_review)
        return f"{self.project}-{number}"

    def as_martian(self) -> martian.MartianEntry:
        """The fields the shared build path needs. The title comes from GitHub at fetch time."""
        return martian.MartianEntry(project=self.project, pr_title="", url=self.pr_url_to_review, comments=[])


def parse_entries(lines: list[str]) -> list[QodoEntry]:
    entries = [QodoEntry.model_validate_json(line) for line in lines if line.strip()]
    for entry in entries:
        if entry.num_of_issues != len(entry.issues):
            raise ValueError(f"{entry.pr_id}: num_of_issues {entry.num_of_issues}, but {len(entry.issues)} issues")
    ids = [entry.pr_id for entry in entries]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"duplicate PR ids: {sorted(duplicates)}")
    return entries


def load_entries(revision: str = PINNED_REVISION) -> list[QodoEntry]:
    url = _URL.format(revision=revision, file=BENCH_FILE)
    with urllib.request.urlopen(url, timeout=60) as response:
        text = response.read().decode("utf-8")
    return parse_entries(text.splitlines())


def describe(title: str, body: str) -> tuple[str, str | None]:
    """Intent text and upstream PR URL from a benchmark PR.

    The benchmark PR's title and body announce it as a benchmark ("Code Review Bench PR #...").
    Both variants get only the upstream PR's own title and description, as Martian's tools did.
    """
    body = body.replace("\r\n", "\n")
    fields: dict[str, str] = {}
    matches = list(_FIELD.finditer(body))
    for m, nxt in zip(matches, [*matches[1:], None]):
        fields[m.group(1)] = body[m.end(): nxt.start() if nxt else len(body)].strip()
    original_title = fields.get("Title") or _TITLE_PREFIX.sub("", title).strip()
    intent = martian.intent_text(original_title, fields.get("Description", ""))
    url = fields.get("URL") or None
    return intent, url


def golden_records(entries: list[QodoEntry]) -> list[GoldenComment]:
    return [
        GoldenComment(pr_id=entry.pr_id, comment=issue.comment(), severity="Unrated", category=issue.category)
        for entry in entries
        for issue in entry.issues
    ]


def build_pull_requests(
    entries: list[QodoEntry],
    data_dir: Path,
    known: dict[str, PullRequest],
    details: Callable[[str], GitHubPR] = martian.fetch_pr_details,
) -> list[PullRequest]:
    return martian.build_pull_requests(
        [entry.as_martian() for entry in entries], data_dir, known, details, describe=describe
    )
