"""PLAN v3, R0 (TASKS J10): diffs as change blocks, for scoring revisions against Qodo's injection.

A Qodo PR's head is the upstream PR plus one commit that plants its issues; its parent (`head^`) is
the clean reference (PLAN v3, 2). `eval/datasets/qodo/injection.jsonl` pins that commit's patch per
PR, from the GitHub API.

Diffs are split into **blocks**: runs of removed and added lines with no context line between them.
GitHub's and git's hunks merge nearby changes through their context lines; blocks don't, so two
planted issues three lines apart stay apart. Each block has a range on each side, in that side's
line numbers. A side with no lines is an insertion point between two lines, and touches both.

- A **revision** diff is head → revised: its old side is in head's line numbers, like the
  injection's new side. A revision block is **on target** when it is within `MARGIN` lines of an
  injection block in the same file; otherwise it is a wrong edit (PLAN v3, 6).
- A **vs-clean** diff is head^ → revised. An injection block is **reverted** when no vs-clean block
  overlaps it on the head^ side: the revised file is back to the clean text there. A planted bug is
  fixed by exact revert when all its blocks are reverted.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .data import ROOT

MARGIN = 3  # lines, as in PLAN v3, 6
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_LOCATION = re.compile(r"\(([^()\s]+?):(\d+)(?:-(\d+))?\)")


@dataclass(frozen=True)
class Block:
    path: str          # the file's path on the new side (the old one for a deletion)
    old: tuple[int, int]  # (first, last) line on the old side; last = first - 1 when it has no lines
    new: tuple[int, int]
    removed: tuple[str, ...] = ()
    added: tuple[str, ...] = ()


def _span(r: tuple[int, int]) -> tuple[int, int]:
    """A range as an interval; an insertion point touches the lines on both sides of it."""
    first, last = r
    return (first - 1, first) if last < first else r


def overlaps(a: tuple[int, int], b: tuple[int, int], margin: int = 0) -> bool:
    (a0, a1), (b0, b1) = _span(a), _span(b)
    return a0 - margin <= b1 and b0 - margin <= a1


def parse_patch(path: str, patch: str) -> list[Block]:
    """The blocks of one file's hunks (the `@@` lines and bodies, as GitHub's `patch` field)."""
    blocks: list[Block] = []
    old = new = 0
    cur: dict | None = None

    def close() -> None:
        nonlocal cur
        if cur:
            blocks.append(Block(path, (cur["o"], old - 1), (cur["n"], new - 1), tuple(cur["rm"]), tuple(cur["add"])))
        cur = None

    for line in patch.splitlines():
        m = _HUNK.match(line)
        if m:
            close()
            a, b, c, d = (int(x) if x is not None else 1 for x in m.groups())
            old, new = (a if b else a + 1), (c if d else c + 1)
        elif line.startswith("\\"):
            continue  # "\ No newline at end of file"
        elif line.startswith(("-", "+")):
            if cur is None:
                cur = {"o": old, "n": new, "rm": [], "add": []}
            if line[0] == "-":
                cur["rm"].append(line[1:])
                old += 1
            else:
                cur["add"].append(line[1:])
                new += 1
        else:  # context
            close()
            old += 1
            new += 1
    close()
    return blocks


def parse_diff(text: str) -> list[Block]:
    """The blocks of a whole `git diff` output (Y15's revision and vs-clean diffs)."""
    blocks: list[Block] = []
    path, old_path, body = None, None, []

    def flush() -> None:
        if path is not None and body:
            blocks.extend(parse_patch(path if path != "/dev/null" else old_path, "\n".join(body)))

    for line in text.splitlines():
        if line.startswith("diff --git "):
            flush()
            path, old_path, body = None, None, []
        elif line.startswith("--- ") and not body:
            old_path = _strip(line[4:])
        elif line.startswith("+++ ") and not body:
            path = _strip(line[4:])
        elif line.startswith("@@") or body:
            body.append(line)
    flush()
    return blocks


def _strip(p: str) -> str:
    p = p.split("\t")[0]
    return p if p == "/dev/null" else p[2:] if p[:2] in ("a/", "b/") else p


def load_injection(dataset_dir: Path = ROOT / "eval" / "datasets" / "qodo") -> dict[str, dict]:
    """{pr_id: {"head", "parent", "blocks": [Block]}} from injection.jsonl."""
    out = {}
    for line in (dataset_dir / "injection.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        blocks = [b for f in row["files"] for b in parse_patch(f["path"], f["patch"])]
        out[row["pr_id"]] = {"head": row["head"], "parent": row["parent"], "blocks": blocks}
    return out


def location(comment: str) -> tuple[str, int, int] | None:
    """The `(file:start-end)` a Qodo golden comment ends with (eval/runner/qodo.py), or None."""
    found = _LOCATION.findall(comment)
    if not found:
        return None
    path, start, end = found[-1]
    return path, int(start), int(end or start)


def bug_blocks(comment: str, injection: list[Block], margin: int = MARGIN) -> list[Block]:
    """The injection blocks a golden comment points at: same file, head lines within `margin`."""
    loc = location(comment)
    if not loc:
        return []
    path, start, end = loc
    return [b for b in injection if b.path == path and overlaps(b.new, (start, end), margin)]


def on_target(block: Block, injection: list[Block], margin: int = MARGIN) -> bool:
    """A revision block (head → revised) near an injection block in the same file."""
    return any(i.path == block.path and overlaps(block.old, i.new, margin) for i in injection)


def reverted(block: Block, vs_clean: list[Block]) -> bool:
    """An injection block the revision undid: no head^ → revised change overlaps it on the head^ side."""
    return not any(v.path == block.path and overlaps(v.old, block.old) for v in vs_clean)
