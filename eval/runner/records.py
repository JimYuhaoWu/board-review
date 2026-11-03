"""Shared dataset records (PLAN 6.3): `prs.jsonl` and `golden.jsonl`, one JSON object per line."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = ROOT / "eval" / "datasets" / "martian"

Record = TypeVar("Record", bound=BaseModel)


class PullRequest(BaseModel):
    """One benchmark PR and the commits a reviewer sees."""

    id: str
    repo: str
    url: str
    original_url: str | None = None
    base: str | None = Field(
        default=None,
        description="Merge-base of github_base and head: where the diff the tools reviewed starts.",
    )
    head: str | None = None
    github_base: str | None = Field(
        default=None,
        description="The PR's base.sha on GitHub. Not always an ancestor of head, so diffs use `base`.",
    )
    intent: str = ""
    runnable: bool
    reason: str | None = None
    files_changed: int | None = None
    additions: int | None = None
    deletions: int | None = None
    agent_files: list[str] = Field(
        default_factory=list,
        description="Agent configuration in the head tree (CLAUDE.md, AGENTS.md, .claude/, .mcp.json).",
    )


class GoldenComment(BaseModel):
    """One expected issue for a PR."""

    pr_id: str
    comment: str
    severity: str
    category: str


def read_jsonl(path: Path, model: type[Record]) -> list[Record]:
    with path.open(encoding="utf-8") as f:
        return [model.model_validate_json(line) for line in f if line.strip()]


def write_jsonl(path: Path, records: Iterable[BaseModel]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for record in records:
            f.write(json.dumps(record.model_dump(mode="json"), ensure_ascii=False) + "\n")
