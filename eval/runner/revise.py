"""The reviser (PLAN v3, TASKS Y15): its prompt, its input file, its repository, and its outputs.

A Qodo PR's head is the upstream PR plus one commit that plants the issues, so `head^` holds the
clean code, the answer. The reviser works in a clone whose history has no `head^`: one commit with
head's tree whose only parent is the base. `vs-clean.diff` is computed afterwards in the PR's own
repository, outside the reviser's reach.

The reviser's input file and folders are neutral: it can't tell which input (N1, C5 or G) it was
given.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

from eval.runner.stream import parse_board_json

INPUTS = ("N1", "C5", "G")
ACTIONS = ("fixed", "declined")
# Frozen after R1 (PLAN v3, 3): meta.json records its hash.
PROMPT = """You are revising a pull request from a code review's findings. The PR is `HEAD~1..HEAD` in the current repository.
<intent-line>
- The review's findings: <findings-file>. Each finding has an id (F1, F2, ...) and one or more wordings of the same issue.

For each finding:
- If you judge it correct, fix it in the working tree with the smallest change that resolves it.
- If you judge it wrong, decline it and say why.

Change nothing else: no refactoring, reformatting, or other fixes. Don't commit.
You can read any file and use read-only git commands.

End with only this JSON report, one entry per finding id in the file:
{"findings": [{"id": "F1", "action": "fixed" or "declined", "reason": "<one sentence>"}]}"""
PROMPT_SHA256 = hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()[:12]


def prompt(intent_file: Path | None, findings_file: Path) -> str:
    intent = f"- The PR's title and description: {intent_file.as_posix()}" if intent_file else ""
    text = PROMPT.replace("<findings-file>", findings_file.as_posix())
    return text.replace("<intent-line>\n", f"{intent}\n" if intent else "")


def load_findings(path: Path, dataset: str, pr_id: str, input_: str, repeat: int) -> list[dict[str, Any]]:
    """The findings of a `revise-inputs` file (TASKS, "Revision inputs"), after checking it is this run's."""
    data = json.loads(path.read_text(encoding="utf-8"))
    want = {"schema_version": 1, "dataset": dataset, "pr_id": pr_id, "input": input_, "repeat": repeat}
    if wrong := [f"{k} {data.get(k)!r}" for k, v in want.items() if data.get(k) != v]:
        raise ValueError(f"{path}: {', '.join(wrong)}, expected {want}")
    findings = data.get("findings")
    if not isinstance(findings, list) or [f.get("id") for f in findings] != [f"F{i}" for i in range(1, len(findings) + 1)]:
        raise ValueError(f"{path}: finding ids must be F1, F2, ... in order")
    if not all(isinstance(f.get("texts"), list) and f["texts"] and all(isinstance(t, str) for t in f["texts"]) for f in findings):
        raise ValueError(f"{path}: every finding needs a non-empty list of texts")
    return findings


def neutral_findings(findings: list[dict[str, Any]]) -> str:
    """What the reviser reads: ids and wordings only, no input name or provenance."""
    payload = {"findings": [{"id": f["id"], "texts": f["texts"]} for f in findings]}
    return json.dumps(payload, ensure_ascii=False, indent=1) + "\n"


def parse_report(text: str, ids: list[str]) -> tuple[dict[str, Any], str]:
    """The reviser's final JSON report, with one entry per finding id. Returns (report, repair)."""
    report, repair = parse_board_json(text)
    entries = report.get("findings")
    if not isinstance(entries, list):
        raise ValueError("report has no findings list")
    seen = [e.get("id") if isinstance(e, dict) else None for e in entries]
    if sorted(seen, key=str) != sorted(ids):
        raise ValueError(f"report ids {seen}, expected {ids}")
    if bad := [e["id"] for e in entries if e.get("action") not in ACTIONS or not isinstance(e.get("reason"), str)]:
        raise ValueError(f"report entries without an action in {ACTIONS} and a reason: {bad}")
    return report, repair


def make_clone(repo: Path, base: str, head: str, dest: Path) -> None:
    """A clone of `repo` holding base -> head as one commit, on the branch `pr`, with no remote or reflog."""
    identity = ["-c", "user.name=revise", "-c", "user.email=revise@localhost"]
    squashed = _git("-C", str(repo), *identity, "commit-tree", f"{head}^{{tree}}", "-p", base, "-m", "PR").strip()
    branch = f"revise-tmp-{hashlib.sha256(str(dest).encode()).hexdigest()[:12]}"
    _git("-C", str(repo), "branch", "--force", branch, squashed)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        # --no-local makes --depth apply to a local path; depth 2 is the squashed commit and the base.
        _git("clone", "--quiet", "--no-local", "--depth", "2", "--branch", branch,
             "-c", "core.autocrlf=false", "-c", "core.longpaths=true", str(repo), str(dest))
    finally:
        _git("-C", str(repo), "branch", "--quiet", "-D", branch)
    _git("-C", str(dest), "branch", "--quiet", "-m", "pr")
    _git("-C", str(dest), "remote", "remove", "origin")
    _git("-C", str(dest), "reflog", "expire", "--expire=now", "--all")


def revision_diff(clone: Path) -> bytes:
    """The reviser's edits against the squashed head, new files included."""
    _git("-C", str(clone), "add", "-A")
    return _git_bytes("-C", str(clone), "diff", "--cached", "--binary", "HEAD")


def vs_clean_diff(repo: Path, head: str, revision: bytes, scratch: Path) -> bytes:
    """The revised tree against `head^`: `revision` applied to head in a scratch index of `repo`."""
    scratch.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GIT_INDEX_FILE": str(scratch / "vs-clean.index")}
    _git("-C", str(repo), "read-tree", head, env=env)
    if revision:
        patch = scratch / "revision.diff"
        patch.write_bytes(revision)
        _git("-C", str(repo), "apply", "--cached", "--binary", str(patch), env=env)
    return _git_bytes("-C", str(repo), "diff", "--cached", "--binary", f"{head}^", env=env)


def remove_tree(path: Path) -> None:
    """rmtree that also removes git's read-only object files on Windows."""

    def make_writable(func, failed: str, _exc) -> None:
        os.chmod(failed, stat.S_IWRITE)
        func(failed)

    if os.name == "nt":
        # Deep repositories pass Windows' 260-character limit without the long-path prefix.
        path = Path("\\\\?\\" + str(path.resolve()))
    if path.exists():
        # onexc replaced onerror in 3.12; the handler ignores the third argument either way.
        shutil.rmtree(path, **({"onexc": make_writable} if sys.version_info >= (3, 12) else {"onerror": make_writable}))


def _git(*args: str, env: dict[str, str] | None = None) -> str:
    return _git_bytes(*args, env=env).decode("utf-8", "replace")


def _git_bytes(*args: str, env: dict[str, str] | None = None) -> bytes:
    """Bytes, because diffs must keep each file's own line endings and encoding."""
    result = subprocess.run(["git", *args], capture_output=True, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {result.stderr.decode('utf-8', 'replace').strip()}")
    return result.stdout
