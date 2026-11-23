"""Review, then revise: the pipeline measured in this repository (PLAN v2 Phase B, PLAN v3 R2).

1. Review: `/code-review medium` in 5 independent `claude -p` sessions, read-only.
2. Match: one `claude -p` call groups the findings that report the same defect (PLAN v2's frozen
   matching prompt); only the issues all 5 reviews found are kept.
3. Revise: one `claude -p` session fixes each kept issue in the working tree with the smallest
   change, or declines it with a reason (PLAN v3's frozen reviser prompt). Nothing is committed.

Standard library only. Every session's output is kept under `<git dir>/review-revise/<time>/`.
Run `python review_revise.py --help` for the options.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

MODEL = "claude-sonnet-5-5"
EFFORT = "medium"
RUNS = 5
TIMEOUT_S = 2 * 60 * 60

# Copied verbatim from eval/scoring/consensus.py (PLAN v2, frozen 2026-11-18); a test checks they match.
MATCH_PROMPT = """You are given the findings of {k} independent code reviews of one pull request.
Group the findings that report the same defect.

Two findings report the same defect only if fixing one would fix the other. Being in the same file, \
touching the same function, or sharing a theme is not enough. If you are unsure, keep them apart.
Findings from one review can also be duplicates of each other.

Findings:
{findings}

Answer with JSON only, in this form, where every finding number appears exactly once and a finding \
that matches nothing is a group of its own:
{{"groups": [[0, 3], [1], [2, 4]]}}"""

# eval/runner/revise.py's PROMPT (PLAN v3, frozen after R1 as 53a4f25dfbce), with one change: the
# change under revision is `<range>`, not the benchmark's squashed `HEAD~1..HEAD`. A test checks it.
REVISE_PROMPT = """You are revising a pull request from a code review's findings. The PR is `<range>` in the current repository.
<intent-line>
- The review's findings: <findings-file>. Each finding has an id (F1, F2, ...) and one or more wordings of the same issue.

For each finding:
- If you judge it correct, fix it in the working tree with the smallest change that resolves it.
- If you judge it wrong, decline it and say why.

Change nothing else: no refactoring, reformatting, or other fixes. Don't commit.
Edit files with the Edit tool.
You can read any file and use read-only git commands.

End with only this JSON report, one entry per finding id in the file:
{"findings": [{"id": "F1", "action": "fixed" or "declined", "reason": "<one sentence>"}]}"""

_READ_ONLY = ["git diff", "git show", "git log", "git grep", "git blame", "git rev-parse",
              "git merge-base", "git status", "git ls-files", "git cat-file", "ls"]
_BASH_READ_ONLY = ["Bash(ls)", *[f"Bash({c} *)" for c in _READ_ONLY]]
# The tools of the benchmark's sessions (eval/runner/run.py).
REVIEW_TOOLS = (["Read", "Grep", "Glob", "Bash", "Task", "ReportFindings"],
                ["Read", "Grep", "Glob", "Task", "ReportFindings", *_BASH_READ_ONLY],
                ["Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch"])
REVISE_TOOLS = (["Read", "Grep", "Glob", "Bash", "Edit", "Write"],
                ["Read", "Grep", "Glob", "Edit", "Write", *_BASH_READ_ONLY],
                ["NotebookEdit", "WebFetch", "WebSearch"])

_CR_ITEM = re.compile(r"^\s*(?:\*\*)?(\d+)[.)](?:\*\*)?\s+`([^`]+)`\s*[:—-]?\s*(.+?)\s*$", re.M)


# --- git -----------------------------------------------------------------------------------------

def git(repo: Path, *args: str, check: bool = True) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if check and done.returncode:
        raise SystemExit(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout.strip()


def default_base(repo: Path) -> str:
    """The merge base of HEAD with its upstream, else with origin's default branch, main or master."""
    for ref in ("@{upstream}", "origin/HEAD", "origin/main", "origin/master", "main", "master"):
        if git(repo, "rev-parse", "--verify", "--quiet", ref, check=False):
            base = git(repo, "merge-base", ref, "HEAD", check=False)
            if base and base != git(repo, "rev-parse", "HEAD"):
                return base
    raise SystemExit("no base found: pass the range to review, for example main..HEAD")


def resolve_range(repo: Path, target: str | None) -> tuple[str, str]:
    """(base sha, HEAD sha). The range must end at HEAD, because the reviser edits the working tree."""
    head = git(repo, "rev-parse", "HEAD")
    if not target:
        return default_base(repo), head
    left, sep, right = target.partition("..")
    if not sep:
        raise SystemExit(f"{target!r} is not a range; pass base..HEAD")
    right = right.lstrip(".") or "HEAD"
    if git(repo, "rev-parse", right) != head:
        raise SystemExit(f"the range must end at HEAD ({head[:12]}), since the revision edits the working tree")
    base = git(repo, "rev-parse", left)
    return (git(repo, "merge-base", base, head) if "..." in target else base), head


def tracked_changes(repo: Path) -> list[str]:
    return [l for l in git(repo, "status", "--porcelain", "--untracked-files=no").splitlines() if l.strip()]


# --- claude -p -----------------------------------------------------------------------------------

def claude_env() -> dict[str, str]:
    """This environment without the calling session's Claude settings, so each `claude -p` starts
    fresh on the user's login. An ANTHROPIC_API_KEY is dropped too: sessions bill the login, not the API."""
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("ANTHROPIC_") or k == "CLAUDECODE"
                   or (k.startswith("CLAUDE_CODE_") and k != "CLAUDE_CODE_GIT_BASH_PATH"))}
    if os.name == "nt" and "CLAUDE_CODE_GIT_BASH_PATH" not in env:
        exec_path = Path(subprocess.run(["git", "--exec-path"], capture_output=True, text=True).stdout.strip())
        bash = next((r / "bin" / "bash.exe" for r in exec_path.parents if (r / "bin" / "bash.exe").exists()), None)
        if bash:
            env["CLAUDE_CODE_GIT_BASH_PATH"] = str(bash)
    return env


def claude_exe() -> str:
    exe = shutil.which("claude")
    if not exe:
        raise SystemExit("claude is not on PATH")
    return exe


def session(prompt: str, cwd: Path, out: Path, tools: tuple, model: str, add_dir: Path | None = None) -> dict:
    """One `claude -p` session as the benchmark ran it. Saves the stream; returns the result event."""
    allowed, permitted, denied = tools
    cmd = [claude_exe(), "-p", prompt, *(["--add-dir", str(add_dir)] if add_dir else []),
           "--model", model, "--effort", EFFORT, "--output-format", "stream-json", "--verbose",
           "--setting-sources", "user", "--strict-mcp-config", "--tools", ",".join(allowed),
           "--allowedTools", *permitted, "--disallowedTools", *denied,
           "--permission-mode", "dontAsk", "--no-session-persistence"]
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "stream.jsonl", "w", encoding="utf-8", newline="\n") as f:
        done = subprocess.run(cmd, cwd=cwd, env=claude_env(), stdin=subprocess.DEVNULL, stdout=f,
                              stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                              timeout=TIMEOUT_S)
    result = next((e for e in reversed(read_stream(out / "stream.jsonl")) if e.get("type") == "result"), None)
    if done.returncode or not result or result.get("is_error"):
        detail = (result or {}).get("result") or done.stderr[-500:]
        raise RuntimeError(f"claude session failed ({out.name}): {str(detail)[:500]}")
    (out / "final.md").write_text(result.get("result") or "", encoding="utf-8", newline="\n")
    return result


def ask(prompt: str, model: str) -> tuple[str, float]:
    """One tool-less `claude -p` call in an empty folder (the matching step)."""
    import tempfile
    with tempfile.TemporaryDirectory() as cwd:
        done = subprocess.run([claude_exe(), "-p", "--model", model, "--effort", EFFORT, "--output-format", "json",
                               "--tools", "", "--strict-mcp-config", "--no-session-persistence"],
                              input=prompt, cwd=cwd, env=claude_env(), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=900)
    if done.returncode:
        raise RuntimeError(f"claude exited {done.returncode}: {(done.stdout or done.stderr)[:300]}")
    reply = json.loads(done.stdout)
    if reply.get("is_error"):
        raise RuntimeError(f"claude reported an error: {str(reply.get('result'))[:300]}")
    return reply["result"], reply.get("total_cost_usd", 0.0)


def read_stream(path: Path) -> list[dict]:
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(e, dict):
            events.append(e)
    return events


# --- findings ------------------------------------------------------------------------------------

def report_findings(events: list[dict]) -> list[dict] | None:
    """The findings of the last ReportFindings call the tool accepted, or None if there was none."""
    calls, accepted = {}, None
    for e in events:
        msg = e.get("message")
        if not isinstance(msg, dict) or not isinstance(msg.get("content"), list):
            continue
        for b in msg["content"]:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("name") == "ReportFindings":
                calls[b.get("id")] = (b.get("input") or {}).get("findings", [])
            elif b.get("type") == "tool_result" and b.get("tool_use_id") in calls and not b.get("is_error"):
                accepted = calls[b["tool_use_id"]]
    return accepted


def parse_reply(text: str) -> list[dict]:
    """Findings from `/code-review`'s text reply (numbered items starting with a `file:line`)."""
    found = []
    for m in _CR_ITEM.finditer(text):
        path, _, rest = m.group(2).partition(":")
        line = re.match(r"\d+", rest)
        found.append({"file": path, "line": int(line.group()) if line else None, "summary": m.group(3)})
    return found


def run_findings(out: Path) -> list[str]:
    """One review's findings, each rendered as `file:line`: statement (as the benchmark matched them).
    An accepted ReportFindings call wins, unless it was empty and the reply lists findings."""
    found = report_findings(read_stream(out / "stream.jsonl"))
    if not found:
        found = parse_reply((out / "final.md").read_text(encoding="utf-8")) or found or []
    texts = []
    for f in found:
        text = f.get("summary") or f.get("short_summary") or ""
        if f.get("file"):
            loc = f"`{f['file']}:{f['line']}`" if f.get("line") else f"`{f['file']}`"
            text = f"{loc}: {text}"
        texts.append(text)
    return texts


def parse_groups(text: str, n: int) -> list[list[int]]:
    found = re.search(r"\{.*\}", text, re.S)
    if not found:
        raise ValueError("no JSON object in the reply")
    groups = json.loads(found.group(0))["groups"]
    if sorted(i for g in groups for i in g) != list(range(n)):
        raise ValueError(f"groups do not cover findings 0..{n - 1} exactly once")
    return groups


def keep_all(groups: list[list[int]], run_of: list[int], runs: int) -> list[list[int]]:
    """The groups every run contributed to: the issues all `runs` reviews found."""
    return [g for g in groups if len({run_of[i] for i in g}) == runs]


def findings_file(texts: list[str], kept: list[list[int]]) -> dict:
    """The reviser's input: neutral ids and every distinct wording of each kept issue."""
    return {"findings": [{"id": f"F{j + 1}", "texts": list(dict.fromkeys(texts[i] for i in g))}
                         for j, g in enumerate(kept)]}


def parse_report(text: str, ids: list[str]) -> dict:
    """The reviser's JSON report: the whole reply, a fenced block, or the last object with findings."""
    candidates = [text.strip(), re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())]
    decoder = json.JSONDecoder()
    candidates += [text[i:] for i in range(len(text) - 1, -1, -1) if text[i] == "{"]
    for c in candidates:
        try:
            obj, _ = decoder.raw_decode(c)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("findings"), list):
            got = [e.get("id") for e in obj["findings"] if isinstance(e, dict)]
            if sorted(got) == sorted(ids):
                return obj
    raise ValueError("no JSON report with one entry per finding id in the reply")


# --- the pipeline --------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("range", nargs="?", help="the change to review, base..HEAD; default: the branch since it left its upstream or main")
    p.add_argument("--intent", help="what the change is meant to do; default: the range's commit messages")
    p.add_argument("--review-only", action="store_true", help="stop after the review: no edits")
    p.add_argument("--runs", type=int, default=RUNS, help=f"independent reviews (default {RUNS}, the measured setting)")
    p.add_argument("--model", default=MODEL, help=f"model of every session (default {MODEL}, the measured setting)")
    p.add_argument("--repo", type=Path, default=Path.cwd())
    a = p.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    repo = Path(git(a.repo, "rev-parse", "--show-toplevel"))
    base, head = resolve_range(repo, a.range)
    rng = f"{base[:12]}..{head[:12]}"
    if not a.review_only and (dirty := tracked_changes(repo)):
        raise SystemExit("commit or stash your changes first; the revision is left as uncommitted edits:\n" + "\n".join(dirty[:10]))
    intent = a.intent or git(repo, "log", "--reverse", "--format=%s%n%n%b", f"{base}..{head}")
    git_dir = Path(git(repo, "rev-parse", "--absolute-git-dir"))
    out = git_dir / "review-revise" / datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    print(f"Reviewing {rng} with {a.runs} independent `/code-review {EFFORT}` sessions ({a.model}); output in {out}", flush=True)

    review_prompt = f"/code-review {EFFORT} --max-findings all {base}..{head}" + (f"\n\n{intent}" if intent else "")
    cost = 0.0

    def review(k: int) -> dict:
        try:
            return session(review_prompt, repo, out / f"review-{k}", REVIEW_TOOLS, a.model)
        except RuntimeError:  # one retry, as a passing API error is the usual cause
            return session(review_prompt, repo, out / f"review-{k}", REVIEW_TOOLS, a.model)

    with concurrent.futures.ThreadPoolExecutor(a.runs) as pool:
        results = list(pool.map(review, range(1, a.runs + 1)))
    cost += sum(r.get("total_cost_usd", 0.0) for r in results)

    texts, run_of = [], []
    for k in range(1, a.runs + 1):
        found = run_findings(out / f"review-{k}")
        print(f"  review {k}: {len(found)} findings", flush=True)
        texts += found
        run_of += [k] * len(found)

    kept: list[list[int]] = []
    if texts and a.runs > 1:
        listing = "\n".join(f"{i}. (review {run_of[i]}) {t}" for i, t in enumerate(texts))
        error = None
        for _ in range(3):
            reply, spent = ask(MATCH_PROMPT.format(k=a.runs, findings=listing), a.model)
            cost += spent
            try:
                groups = parse_groups(reply, len(texts))
                break
            except (ValueError, KeyError, TypeError) as e:
                error = e
        else:
            raise SystemExit(f"the matching step gave no valid groups after 3 tries: {error}")
        (out / "match.json").write_text(json.dumps({"findings": texts, "run_of": run_of, "groups": groups},
                                                   ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        kept = keep_all(groups, run_of, a.runs)
    elif texts:
        kept = [[i] for i in range(len(texts))]
    payload = findings_file(texts, kept)
    inputs = out / "inputs"
    inputs.mkdir()
    (inputs / "findings.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    if intent:
        (inputs / "intent.txt").write_text(intent + "\n", encoding="utf-8", newline="\n")

    lines = [f"# Review and revise: {rng}", "",
             f"{a.runs} reviews found {len(texts)} findings; **{len(kept)} issues were found by all {a.runs}** and kept.", ""]
    for f in payload["findings"]:
        lines.append(f"- **{f['id']}** {f['texts'][0]}")

    if kept and not a.review_only:
        print(f"Revising {len(kept)} kept issues in the working tree", flush=True)
        prompt = (REVISE_PROMPT.replace("<range>", f"{base}..HEAD")
                  .replace("<findings-file>", (inputs / "findings.json").as_posix())
                  .replace("<intent-line>\n", f"- The PR's title and description: {(inputs / 'intent.txt').as_posix()}\n" if intent else ""))
        result = session(prompt, repo, out / "revise", REVISE_TOOLS, a.model, add_dir=inputs)
        cost += result.get("total_cost_usd", 0.0)
        ids = [f["id"] for f in payload["findings"]]
        lines += ["", "## Revision (uncommitted)", ""]
        try:
            report = parse_report(result.get("result") or "", ids)
            (out / "revise" / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            for e in report["findings"]:
                lines.append(f"- **{e['id']}** {e.get('action')}: {e.get('reason', '')}")
        except ValueError as e:
            lines.append(f"The reviser's report could not be read ({e}); its reply is in {out / 'revise' / 'final.md'}.")
        stat = git(repo, "diff", "--stat")
        lines += ["", "```", stat or "(no edits)", "```", "",
                  "Check `git diff` before committing: in the benchmark, about 1.4 edits per PR fixed nothing "
                  "and about 1.3 planted bugs per PR were still left."]
    elif kept:
        lines += ["", "Review only: nothing was edited."]
    lines += ["", f"Cost: ${cost:.2f} (API-equivalent). Sessions: {out}"]
    summary = "\n".join(lines) + "\n"
    (out / "summary.md").write_text(summary, encoding="utf-8")
    print("\n" + summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
