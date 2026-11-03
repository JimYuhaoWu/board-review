"""Read datasets and run results in the layout of docs/TASKS.md."""

import json
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


def iter_runs(dataset: str, variants: list[str], pr_ids: list[str]) -> list[Run]:
    """Finished runs (meta status ok) for the given variants and PRs."""
    runs = []
    for variant in variants:
        for pid in pr_ids:
            pr_dir = RESULTS / dataset / variant / pid
            if not pr_dir.is_dir():
                continue
            for rdir in sorted(pr_dir.glob("r*")):
                meta_file = rdir / "meta.json"
                if not meta_file.exists():
                    continue
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                if meta.get("status") != "ok":
                    continue
                runs.append(Run(dataset, variant, pid, int(rdir.name[1:]), rdir, meta))
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


def review_findings(run: Run) -> list[dict] | None:
    """A run's findings as {id, member, label, file, line, text}; None when `/code-review`
    reported no structured findings (then its text reply is scored whole).

    `text` is the finding's one-sentence statement: the board's `claim`, `/code-review`'s
    `summary`. Evidence, failure scenarios, and fixes are left out for both, so neither
    variant's longer explanations get extracted into extra issues.
    """
    if run.variant == "code-review":
        found = _report_findings(run.path / "stream.jsonl") if (run.path / "stream.jsonl").exists() else None
        if found is None:
            return None
        return [
            {"id": f"cr-{i + 1}", "member": None, "label": None, "file": f.get("file"), "line": f.get("line"),
             "text": f.get("summary") or f.get("short_summary", "")}
            for i, f in enumerate(found)
        ]
    board = parse_board_output((run.path / "final.json").read_text(encoding="utf-8"))
    return [
        {"id": f["id"], "member": f["member"], "label": f.get("label"), "file": f.get("file"), "line": f.get("line"),
         "text": f["claim"]}
        for f in board["findings"]
    ]


def render_finding(f: dict) -> str:
    """One finding as a review comment: its location and statement."""
    if f.get("file"):
        loc = f"`{f['file']}:{f['line']}`" if f.get("line") else f"`{f['file']}`"
        return f"{loc}: {f['text']}"
    return f["text"]
