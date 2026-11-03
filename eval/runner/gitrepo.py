"""Git plumbing for benchmark repositories.

Each project has one bare cache repository holding the PR heads and bases fetched from GitHub.
Each PR then gets its own bare repository with only the history of its head: no other branches,
no tags, no remote. A reviewer working in it can't find later commits, such as the upstream fix
for the bug under review (PLAN Phase 2 step 3).
"""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath

AGENT_FILE_NAMES = {"CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", ".mcp.json"}


def git(*args: str, cwd: Path | None = None, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def ensure_bare(path: Path) -> None:
    if not (path / "HEAD").exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        git("init", "--quiet", "--bare", str(path))
    git("-C", str(path), "config", "core.autocrlf", "false")


def fetch(repo: Path, remote: str, refspec: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "fetch", "--quiet", "--no-tags", remote, refspec],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def has_commit(repo: Path, sha: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True
    )
    return result.returncode == 0


def rev_parse(repo: Path, ref: str) -> str:
    return git("-C", str(repo), "rev-parse", "--verify", f"{ref}^{{commit}}").strip()


def update_ref(repo: Path, ref: str, sha: str) -> None:
    git("-C", str(repo), "update-ref", ref, sha)


def merge_base(repo: Path, a: str, b: str) -> str | None:
    out = git("-C", str(repo), "merge-base", a, b, check=False).strip()
    return out or None


def diff_stats(repo: Path, base: str, head: str) -> tuple[int, int, int]:
    """Files changed, lines added, and lines removed between two commits."""
    files = added = removed = 0
    for line in git("-C", str(repo), "diff", "--numstat", base, head).splitlines():
        plus, minus, _ = line.split("\t", 2)
        files += 1
        # Binary files show "-" for both counts.
        added += int(plus) if plus.isdigit() else 0
        removed += int(minus) if minus.isdigit() else 0
    return files, added, removed


def agent_files(repo: Path, commit: str) -> list[str]:
    """Agent configuration files in a commit's tree, which Claude Code could load in a run."""
    names = git("-C", str(repo), "ls-tree", "-r", "-z", "--name-only", commit).split("\0")
    found = []
    for name in filter(None, names):
        path = PurePosixPath(name)
        if path.name in AGENT_FILE_NAMES or ".claude" in path.parts:
            found.append(name)
    return found


def build_pr_repo(cache: Path, dest: Path, ref: str, head: str) -> None:
    """Create `dest` holding only `head` and its ancestors, as the single branch `pr`."""
    if (dest / "HEAD").exists() and git("-C", str(dest), "for-each-ref", "--format=%(objectname)").split() == [head]:
        return
    ensure_bare(dest)
    git("-C", str(dest), "fetch", "--quiet", "--no-tags", str(cache), f"+{ref}:refs/heads/pr")
    git("-C", str(dest), "symbolic-ref", "HEAD", "refs/heads/pr")
    if rev_parse(dest, "refs/heads/pr") != head:
        raise RuntimeError(f"{dest}: refs/heads/pr is not {head}")
