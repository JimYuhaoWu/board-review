"""Read datasets and run results in the layout of docs/TASKS.md."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATASETS = ROOT / "eval" / "datasets"
RESULTS = ROOT / "eval" / "results"
ROSTER_FILE = ROOT / "plugin" / "skills" / "review" / "roster.json"

LABELS = ("CONFIRMED", "PLAUSIBLE", "REFUTED")


def read_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


@dataclass
class Dataset:
    name: str
    prs: dict[str, dict]
    golden: dict[str, list[dict]]
    split: dict[str, list[str]]

    def pr_ids(self, split: str) -> list[str]:
        """Runnable PR ids in a split: 'dev', 'test', or 'all'."""
        runnable = [pid for pid, pr in self.prs.items() if pr.get("runnable", True)]
        if split == "all":
            return runnable
        chosen = set(self.split.get(split, []))
        return [pid for pid in runnable if pid in chosen]


def load_dataset(name: str) -> Dataset:
    d = DATASETS / name
    prs = {pr["id"]: pr for pr in read_jsonl(d / "prs.jsonl")}
    golden: dict[str, list[dict]] = {pid: [] for pid in prs}
    for gc in read_jsonl(d / "golden.jsonl"):
        if gc["pr_id"] not in golden:
            raise ValueError(f"golden comment for unknown PR {gc['pr_id']!r}")
        golden[gc["pr_id"]].append(
            {"comment": gc["comment"], "severity": gc.get("severity"), "category": gc.get("category")}
        )
    split_file = d / "split.json"
    split = json.loads(split_file.read_text(encoding="utf-8")) if split_file.exists() else {}
    return Dataset(name, prs, golden, split)


def default_roster() -> list[str]:
    return json.loads(ROSTER_FILE.read_text(encoding="utf-8"))["default"]


def member_categories(member: str, members_dir: Path = ROSTER_FILE.parent / "members") -> list[str]:
    """The golden categories a member's brief targets (its `categories:` frontmatter)."""
    path = members_dir / f"{member}.md"
    if not path.exists():
        return []
    m = re.search(r"^categories:\s*\[([^\]]*)\]", path.read_text(encoding="utf-8"), re.MULTILINE)
    return [c.strip() for c in m.group(1).split(",") if c.strip()] if m else []


# Relative list prices per token type, so usage from different token types can be added up.
_PRICE = {"input_tokens": 1.0, "output_tokens": 5.0, "cache_read_input_tokens": 0.1,
          "ephemeral_5m_input_tokens": 1.25, "ephemeral_1h_input_tokens": 2.0}


def subagent_usage(stream: Path) -> dict[str, float]:
    """Price-weighted token usage per board member in one run's stream.jsonl, with all
    verifiers under "verifier". Members are recognized by the brief their prompt names."""
    who: dict[str, str] = {}
    usage: dict[str, float] = {}
    with open(stream, encoding="utf-8") as f:
        for line in f:
            event = json.loads(line)
            content = (event.get("message") or {}).get("content") if isinstance(event.get("message"), dict) else None
            for c in content if isinstance(content, list) else []:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool_use" and c.get("name") in ("Agent", "Task"):
                    inp = json.dumps(c.get("input"))
                    m = re.search(r"members/([\w-]+)\.md", inp)
                    who[c["id"]] = m.group(1) if m else "verifier" if "board-verifier" in inp else "other"
                elif c.get("type") == "tool_result" and c.get("tool_use_id") in who:
                    u = (event.get("tool_use_result") or {}).get("usage") if isinstance(event.get("tool_use_result"), dict) else None
                    if not u:
                        continue
                    cost = sum(_PRICE[k] * u.get(k, 0) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens"))
                    cache = u.get("cache_creation") or {}
                    cost += sum(_PRICE[k] * cache.get(k, 0) for k in ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens"))
                    if not cache:
                        cost += 1.25 * u.get("cache_creation_input_tokens", 0)
                    usage[who[c["tool_use_id"]]] = usage.get(who[c["tool_use_id"]], 0.0) + cost
    return usage


@dataclass
class Run:
    dataset: str
    variant: str
    pr_id: str
    repeat: int
    path: Path
    meta: dict

    @property
    def key(self) -> str:
        return f"{self.variant}/{self.pr_id}/r{self.repeat}"


# Pseudo-variant -> two-session variant: the `/code-review` session inside each of its runs
# (`<run>/code-review/`), judged alone as a `/code-review` run. `code-review-b1` is the reference
# for Gate 4's recall floor (PLAN Phase 3, Gate 4, rule 2).
NESTED_REVIEWS = {"code-review-b1": "board-none"}


def iter_runs(dataset: str, variants: list[str], pr_ids: list[str]) -> list[Run]:
    """Finished runs (meta status ok) for the given variants and PRs. A nested review counts only
    when both its own session and its parent run finished."""
    runs = []
    for variant in variants:
        parent = NESTED_REVIEWS.get(variant)
        for pid in pr_ids:
            pr_dir = RESULTS / dataset / (parent or variant) / pid
            if not pr_dir.is_dir():
                continue
            for rdir in sorted(pr_dir.glob("r*")):
                metas = [rdir / "meta.json"] + ([rdir / "code-review" / "meta.json"] if parent else [])
                if not all(m.exists() for m in metas):
                    continue
                loaded = [json.loads(m.read_text(encoding="utf-8")) for m in metas]
                if any(m.get("status") != "ok" for m in loaded):
                    continue
                path = rdir / "code-review" if parent else rdir
                runs.append(Run(dataset, variant, pid, int(rdir.name[1:]), path, loaded[-1]))
    return runs


def parse_board_output(text: str) -> dict:
    """Parse the board's final JSON, tolerating a code fence or a missing opening brace."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0].strip()
    if not t.startswith("{"):
        t = "{" + t
    return json.loads(t)


def _report_findings(stream: Path) -> list[dict] | None:
    """Findings of the last ReportFindings call that the tool accepted, or None if there was none."""
    calls, accepted = {}, None
    with open(stream, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            msg = json.loads(line).get("message")
            if not isinstance(msg, dict) or not isinstance(msg.get("content"), list):
                continue
            for block in msg["content"]:
                if block.get("type") == "tool_use" and block.get("name") == "ReportFindings":
                    calls[block["id"]] = block["input"].get("findings", [])
                elif block.get("type") == "tool_result" and block.get("tool_use_id") in calls:
                    if not block.get("is_error"):
                        accepted = calls[block["tool_use_id"]]
    return accepted


# `/code-review` at `medium` doesn't call ReportFindings; its reply lists findings as
# ``N. `file:line`: one-sentence summary``, each followed by a "Failure scenario:" line.
_CR_ITEM = re.compile(r"^\s*(?:\*\*)?(\d+)[.)](?:\*\*)?\s+`([^`]+)`\s*[:—-]?\s*(.+?)\s*$", re.M)


_FAILURE = re.compile(r"Failure scenario:\s*(.+)", re.S)


def parse_code_review_text(text: str) -> list[dict]:
    """Findings from `/code-review`'s text reply, as {file, line, summary, failure_scenario};
    [] if none match. The failure scenario is the "Failure scenario:" text up to the next item."""
    found = []
    items = list(_CR_ITEM.finditer(text))
    for m, nxt in zip(items, [*items[1:], None]):
        location, summary = m.group(2), m.group(3)
        path, _, rest = location.partition(":")
        line = re.match(r"\d+", rest)
        tail = _FAILURE.search(text[m.end(): nxt.start() if nxt else len(text)])
        found.append({"file": path, "line": int(line.group()) if line else None, "summary": summary,
                      "failure_scenario": " ".join(tail.group(1).split()) if tail else ""})
    return found


def code_review_found(run_dir: Path) -> list[dict] | None:
    """A `/code-review` run's findings: its accepted ReportFindings call, else its parsed reply.

    An accepted call with no findings gives way to a reply that lists findings. In Gate 3, three
    sessions called ReportFindings with an empty list by mistake, then listed 6 findings in the
    reply and said those were the ones that count (decided 2026-11-06, before scoring).
    """
    found = _report_findings(run_dir / "stream.jsonl") if (run_dir / "stream.jsonl").exists() else None
    if not found and (run_dir / "final.md").exists():
        found = parse_code_review_text((run_dir / "final.md").read_text(encoding="utf-8")) or found
    return found


EXTRA_MEMBER = "code-review"  # PLAN Gate 3, A2: the board's 13th member, run in its own session
_CR_CATEGORY = {"security": "security", "concurrency": "concurrency", "performance": "perf", "efficiency": "perf",
                "test-coverage": "test_gap", "documentation": "doc_defect", "style": "style"}


def code_review_member_output(run_dir: Path) -> dict | None:
    """A finished `/code-review` run as one `member_output` for the board's `--extra-findings`.

    Fixed before any Gate 3 run: `claim` is the finding's summary and `evidence` its failure
    scenario; severity is High for every finding (`/code-review` reports correctness problems on
    realistic paths and gives no severity), so each gets the board's second, independent verifier;
    confidence 0.8 when `/code-review` itself marked the finding CONFIRMED, else 0.6. A finding
    without a file or line keeps an empty file and line 1. None when the run has no parsable findings.
    """
    found = code_review_found(run_dir)
    if found is None:
        return None
    findings = []
    for f in found:
        finding = {
            "file": f.get("file") or "",
            "line": f.get("line") if isinstance(f.get("line"), int) and f["line"] >= 1 else 1,
            "claim": f.get("summary") or f.get("short_summary", ""),
            "evidence": f.get("failure_scenario") or "",
            "severity": "High",
            "category": _CR_CATEGORY.get(str(f.get("category", "")).lower(), "bug"),
            "confidence": 0.8 if f.get("verdict") == "CONFIRMED" else 0.6,
        }
        findings.append(finding)
    return {"member": EXTRA_MEMBER, "findings": findings,
            "notes": f"Findings of a separate /code-review session ({len(findings)})."}


def review_findings(run: Run) -> list[dict] | None:
    """A run's findings as {id, member, label, group, file, line, text}; None when `/code-review`
    gave neither structured findings nor a parsable list (then its text reply is extracted whole).

    `text` is the finding's one-sentence statement: the board's `claim`, `/code-review`'s
    `summary`. Evidence, failure scenarios, and fixes are left out for both, so neither
    variant's longer explanations get extracted into extra issues.
    """
    if not run.variant.startswith("board-"):  # `code-review`, or a nested review (NESTED_REVIEWS)
        found = code_review_found(run.path)
        if found is None:
            return None
        return [
            {"id": f"cr-{i + 1}", "member": None, "label": None, "group": None, "file": f.get("file"), "line": f.get("line"),
             "text": f.get("summary") or f.get("short_summary", "")}
            for i, f in enumerate(found)
        ]
    board = parse_board_output((run.path / "final.json").read_text(encoding="utf-8"))
    return [
        {"id": f["id"], "member": f["member"], "label": f.get("label"), "group": f.get("group"),
         "file": f.get("file"), "line": f.get("line"), "text": f["claim"]}
        for f in board["findings"]
    ]


def representatives(findings: list[dict]) -> list[dict]:
    """One finding per board group: the first, as the board's report shows one item per group.
    Findings without a group (`/code-review`) each stand alone."""
    seen, out = set(), []
    for f in findings:
        key = f.get("group") or f["id"]
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def render_finding(f: dict) -> str:
    """One finding as a review comment: its location and statement."""
    if f.get("file"):
        loc = f"`{f['file']}:{f['line']}`" if f.get("line") else f"`{f['file']}`"
        return f"{loc}: {f['text']}"
    return f["text"]
