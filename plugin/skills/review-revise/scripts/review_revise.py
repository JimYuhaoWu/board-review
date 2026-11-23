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
import tempfile
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


_PR = re.compile(r"^(?:#?(\d+)|https://github\.com/[^/\s]+/[^/\s]+/pull/(\d+)\S*)$")


def gh_pr(repo: Path, number: int | None = None) -> dict | None:
    """The GitHub PR (given, else the current branch's), or None without gh or a PR."""
    exe = shutil.which("gh")
    if not exe:
        return None
    done = subprocess.run([exe, "pr", "view", *([str(number)] if number else []),
                           "--json", "number,title,body,baseRefName,state"],
                          cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return json.loads(done.stdout) if done.returncode == 0 and done.stdout.strip() else None


def fetched(repo: Path, ref: str) -> str:
    git(repo, "fetch", "--quiet", "origin", ref)
    return git(repo, "rev-parse", "FETCH_HEAD")


def default_base(repo: Path, head: str) -> str:
    """The merge base of HEAD with its upstream, else with origin's default branch, main or master."""
    for ref in ("@{upstream}", "origin/HEAD", "origin/main", "origin/master", "main", "master"):
        if git(repo, "rev-parse", "--verify", "--quiet", ref, check=False):
            base = git(repo, "merge-base", ref, head, check=False)
            if base and base != head:
                return base
    raise SystemExit("no base found: pass a PR number or a range such as main..HEAD")


def resolve_target(repo: Path, target: str | None) -> tuple[str, str, str, str]:
    """(base sha, head sha, intent, label) for a PR number or URL, a range, or by default the
    current branch (against its open PR's base when gh finds one)."""
    m = _PR.match((target or "").strip())
    if m:
        n = int(m.group(1) or m.group(2))
        pr = gh_pr(repo, n)
        if not pr:
            raise SystemExit(f"PR {n}: gh cannot read it (is gh installed and logged in, and is this its repository?)")
        head = fetched(repo, f"pull/{n}/head")
        base = git(repo, "merge-base", fetched(repo, pr["baseRefName"]), head)
        return base, head, f"{pr['title']}\n\n{pr.get('body') or ''}".strip(), f"pr-{n}"
    if target:
        left, sep, right = target.partition("..")
        if not sep:
            raise SystemExit(f"{target!r} is neither a PR number nor a range such as main..HEAD")
        head = git(repo, "rev-parse", right.lstrip(".") or "HEAD")
        base = git(repo, "rev-parse", left)
        if "..." in target:
            base = git(repo, "merge-base", base, head)
        return base, head, "", re.sub(r"[^\w-]+", "-", target).strip("-")[:40]
    head = git(repo, "rev-parse", "HEAD")
    label = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    pr = gh_pr(repo)
    if pr and pr.get("state") == "OPEN":
        base = git(repo, "merge-base", fetched(repo, pr["baseRefName"]), head)
        return base, head, f"{pr['title']}\n\n{pr.get('body') or ''}".strip(), f"pr-{pr['number']}"
    return default_base(repo, head), head, "", label if label != "HEAD" else head[:12]


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
    p.add_argument("target", nargs="?", help="a PR number or URL, or a range base..head; default: the current "
                                             "branch, against its open PR's base (gh), else its upstream or main")
    p.add_argument("--intent", help="what the change is meant to do; default: the PR's title and description, "
                                    "else the commit messages")
    p.add_argument("--review-only", action="store_true", help="stop after the review: no edits")
    p.add_argument("--runs", type=int, default=RUNS, help=f"independent reviews (default {RUNS}, the measured setting)")
    p.add_argument("--model", default=MODEL, help=f"model of every session (default {MODEL}, the measured setting)")
    p.add_argument("--repo", type=Path, default=Path.cwd())
    a = p.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    repo = Path(git(a.repo, "rev-parse", "--show-toplevel"))
    base, head, pr_intent, label = resolve_target(repo, a.target)
    if base == head:
        raise SystemExit("nothing to review: the change is empty")
    intent = a.intent or pr_intent or git(repo, "log", "--reverse", "--format=%s%n%n%b", f"{base}..{head}")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(git(repo, "rev-parse", "--absolute-git-dir")) / "review-revise" / stamp
    out.mkdir(parents=True)
    # Everything runs in a temporary worktree at the change's head: the user's working tree, branch and
    # uncommitted changes are never touched, and a PR needn't be checked out.
    tmp = Path(tempfile.mkdtemp(prefix="review-revise-"))
    wt = tmp / "repo"
    git(repo, "worktree", "add", "--quiet", "--detach", str(wt), head)
    print(f"Reviewing {label} ({base[:12]}..{head[:12]}) with {a.runs} independent `/code-review {EFFORT}` "
          f"sessions ({a.model}); output in {out}", flush=True)
    try:
        summary = pipeline(a, repo, wt, out, base, head, intent, label, stamp)
    finally:
        git(repo, "worktree", "remove", "--force", str(wt), check=False)
        shutil.rmtree(tmp, ignore_errors=True)
    (out / "summary.md").write_text(summary, encoding="utf-8")
    print("\n" + summary)
    return 0


def pipeline(a, repo: Path, wt: Path, out: Path, base: str, head: str, intent: str, label: str, stamp: str) -> str:
    review_prompt = f"/code-review {EFFORT} --max-findings all {base}..{head}" + (f"\n\n{intent}" if intent else "")
    cost = 0.0

    def review(k: int) -> dict:
        try:
            return session(review_prompt, wt, out / f"review-{k}", REVIEW_TOOLS, a.model)
        except RuntimeError:  # one retry, as a passing API error is the usual cause
            return session(review_prompt, wt, out / f"review-{k}", REVIEW_TOOLS, a.model)

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

    lines = [f"# Review and revise: {label} ({base[:12]}..{head[:12]})", "",
             f"{a.runs} reviews found {len(texts)} findings; **{len(kept)} issues were found by all {a.runs}** and kept.", ""]
    lines += [f"- **{f['id']}** {f['texts'][0]}" for f in payload["findings"]]

    if kept and not a.review_only:
        print(f"Revising {len(kept)} kept issues", flush=True)
        prompt = (REVISE_PROMPT.replace("<range>", f"{base}..HEAD")
                  .replace("<findings-file>", (inputs / "findings.json").as_posix())
                  .replace("<intent-line>\n", f"- The PR's title and description: {(inputs / 'intent.txt').as_posix()}\n" if intent else ""))
        result = session(prompt, wt, out / "revise", REVISE_TOOLS, a.model, add_dir=inputs)
        cost += result.get("total_cost_usd", 0.0)
        lines += ["", "## Revision", ""]
        actions: list[dict] = []
        try:
            report = parse_report(result.get("result") or "", [f["id"] for f in payload["findings"]])
            (out / "revise" / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            actions = report["findings"]
            lines += [f"- **{e['id']}** {e.get('action')}: {e.get('reason', '')}" for e in actions]
        except ValueError as e:
            lines.append(f"The reviser's report could not be read ({e}); its reply is in {out / 'revise' / 'final.md'}.")
        git(wt, "add", "-A")
        diff = git(wt, "diff", "--cached")
        (out / "revision.diff").write_text(diff + "\n" if diff else "", encoding="utf-8", newline="\n")
        if diff:
            branch = f"review-revise/{label}-{stamp}"
            who = [] if git(repo, "config", "user.email", check=False) else [
                "-c", "user.name=review-revise", "-c", "user.email=review-revise@localhost"]
            message = f"review-revise: fixes for {label}\n\n" + "\n".join(
                f"{e['id']} {e.get('action')}: {e.get('reason', '')}" for e in actions)
            git(wt, *who, "commit", "--quiet", "--no-verify", "-m", message)
            git(wt, "branch", branch)
            lines += ["", "```", git(wt, "show", "--stat", "--format=", "HEAD"), "```", "",
                      f"The fixes are one commit on the new local branch `{branch}`, on top of {head[:12]}. "
                      "Nothing was pushed, and your working tree wasn't touched.",
                      f"- See them: `git show {branch}`",
                      f"- Take them (on the reviewed branch): `git merge --ff-only {branch}`", "",
                      "Check the diff before taking it: on the benchmark's test PRs, about 1.4 edits per PR fixed nothing "
                      "and about 1.3 planted bugs per PR were still left."]
        else:
            lines += ["", "The reviser changed no files, so no branch was made."]
    elif kept:
        lines += ["", "Review only: nothing was edited."]
    lines += ["", f"Cost: ${cost:.2f} (API-equivalent). Sessions: {out}"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
