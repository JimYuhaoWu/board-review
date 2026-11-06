"""Run one (variant, PR, repeat) through `claude -p` and write the layout in docs/TASKS.md.

Each run gets a fresh worktree at the head commit, the pinned command, and the clean config
folder. The runner stops a run as soon as its init event shows a different setup. A run that
hits a plan usage limit is recorded as `failed:usage-limit`, never as an empty review; running
the same command again skips finished runs and retries the rest. A board run is one `claude -p`
call, so a retry re-runs the whole board, not single members. A finished board run made with an
older plugin isn't done: `stale_reason` names what changed, and its folder must be moved aside
before it runs again.

A `board-cr` run (PLAN Phase 3, Gate 3, A2) is two sessions: a `/code-review` session stored in
the run's `code-review/` folder, then the full board with that session's findings as the extra
member `code-review` (`--extra-findings`). A failure in either session leaves the run unfinished,
so a retry redoes both.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from eval.runner import gitrepo
from eval.runner.records import ROOT
from eval.runner.stream import ExpectedSetup, InitInfo, ResultInfo, parse_board_json, setup_problems
from eval.scoring.data import EXTRA_MEMBER, code_review_member_output

PINNED_VERSION = "2.1.317"
MODEL = "claude-sonnet-5-5"
EFFORT = "medium"
VARIANTS = ("code-review", "board-full")
BOARD_CR = "board-cr"
# ReportFindings is how /code-review hands back structured findings, which scoring reads (TASKS).
TOOLS = ["Read", "Grep", "Glob", "Bash", "Task", "ReportFindings"]
_READ_ONLY_COMMANDS = [
    "git diff", "git show", "git log", "git grep", "git blame", "git rev-parse",
    "git merge-base", "git status", "git ls-files", "git cat-file", "ls",
]
ALLOWED_TOOLS = [
    "Read", "Grep", "Glob", "Task", "ReportFindings", "Bash(ls)",
    *[f"Bash({c} *)" for c in _READ_ONLY_COMMANDS],
]
DISALLOWED_TOOLS = ["Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch"]
RUN_TIMEOUT_S = 2 * 60 * 60
PLUGIN_DIR = ROOT / "plugin"
SKILL_FILES = {
    "skill": "skills/review/SKILL.md",
    "schema": "skills/review/schema.json",
    "roster": "skills/review/roster.json",
    "member_agent": "agents/board-member.md",
    "verifier": "agents/board-verifier.md",
}
_ACCOUNTS = {"pro": "A", "team": "B"}


class RunSpec(BaseModel):
    dataset: str
    pr_id: str
    variant: str
    repeat: int
    repo: Path
    base: str
    head: str
    model: str = MODEL
    effort: str = EFFORT
    members: str = "full"
    intent: str = ""


class Paths(BaseModel):
    results: Path
    cache: Path
    work: Path
    config_dir: Path
    claude: str


class RunMeta(BaseModel):
    """Run metadata (PLAN 6.4). Judge fields are added by scoring."""

    dataset: str
    dataset_commit: str
    pr_id: str
    variant: str
    repeat: int
    roster: str | None
    base: str
    head: str
    reviewer_model: str
    effort: str
    intent: str
    claude_code_version: str | None = None
    skill_hashes: dict[str, str]
    config_hash: str
    account: str
    command: list[str]
    started_at: str
    wall_seconds: float | None = None
    status: str = "running"
    detail: str | None = None
    parse_repair: str | None = None
    total_cost_usd: float | None = None
    model_usage: dict[str, Any] = {}
    subagents: dict[str, Any] = {}
    num_turns: int | None = None
    permission_denials: int = 0
    members: dict[str, Any] | None = None
    setup: InitInfo | None = None
    # board-cr only: each session's cost, time, and denials (the top-level ones are their sums),
    # and the hash and size of the extra-findings file the board read.
    sessions: dict[str, dict[str, Any]] = {}
    extra_findings: dict[str, Any] | None = None


def run_name(spec: RunSpec) -> str:
    return f"{spec.dataset}/{spec.variant}/{spec.pr_id}/r{spec.repeat}"


def run_dir(paths: Paths, spec: RunSpec) -> Path:
    return paths.results / run_name(spec)


def is_done(paths: Paths, spec: RunSpec) -> bool:
    meta = run_dir(paths, spec) / "meta.json"
    return meta.exists() and json.loads(meta.read_text(encoding="utf-8")).get("status") == "ok"


def stale_reason(paths: Paths, spec: RunSpec) -> str | None:
    """What changed in the plugin since a finished board run, or None if the run still matches.

    Only the shared skill files and the members that ran or were skipped in that run count.
    /code-review runs don't read the board's files, so they are never stale.
    """
    if not spec.variant.startswith("board-") or not is_done(paths, spec):
        return None
    meta = json.loads((run_dir(paths, spec) / "meta.json").read_text(encoding="utf-8"))
    old, current = meta["skill_hashes"], skill_hashes()
    changed = [name for name in SKILL_FILES if old.get(name) != current[name]]
    if not changed and meta["config_hash"] != config_hash(spec, PINNED_VERSION, current):
        changed = ["effort or Claude Code version"]
    members = meta.get("members") or {}
    took_part = [*members.get("ran", []), *(s["id"] for s in members.get("skipped", []))]
    changed += [f"member {m}" for m in took_part if old.get(f"member:{m}") != current.get(f"member:{m}")]
    return ", ".join(changed) or None


def cache_clash(paths: Paths, spec: RunSpec) -> str | None:
    """The other run whose cache file this run would overwrite, or None.

    The cache path doesn't name the variant. A member run alone while its brief is unchanged
    since a full run of the same PR and repeat would replace that run's file, and would measure
    nothing new. A full run may replace a single-member file: scoring starts from the full run.
    board-cr runs write no cache files.
    """
    if spec.variant in ("board-full", BOARD_CR):
        return None
    hashes = skill_hashes()
    config = config_hash(spec, PINNED_VERSION, hashes)
    for member in spec.members.split(","):
        path = cache_file(paths.cache, spec, member, config, hashes.get(f"member:{member}", "unknown"))
        if path.exists():
            source = json.loads(path.read_text(encoding="utf-8")).get("source_run")
            if source != run_name(spec):
                return source
    return None


def cache_file(cache: Path, spec: RunSpec, member: str, config: str, member_hash: str) -> Path:
    """<dataset>/<pr_id>/r<repeat>/<member>@<model>/<config-hash>/<member-file-hash>.json"""
    return (
        cache / spec.dataset / spec.pr_id / f"r{spec.repeat}"
        / f"{member}@{spec.model}" / config / f"{member_hash}.json"
    )


def prompt_for(spec: RunSpec, intent_file: Path | None, extra_file: Path | None = None) -> str:
    """The variant's command. Both variants get the PR's title and description (PLAN 5.1)."""
    target = f"{spec.base}..{spec.head}"
    if spec.variant == "code-review":
        prompt = f"/code-review medium --max-findings all {target}"
        return f"{prompt}\n\n{spec.intent}" if spec.intent else prompt
    if spec.variant.startswith("board-"):
        # A file, because PR descriptions can contain `--flags` that --intent would misparse.
        intent = f"--intent-file {intent_file.as_posix()} " if intent_file else ""
        extra = f"--extra-findings {extra_file.as_posix()} " if extra_file else ""
        return f"/board-review:review --members {spec.members} --max-findings 8 --format json {intent}{extra}{target}"
    raise ValueError(f"unknown variant {spec.variant}")


def command_for(
    spec: RunSpec, claude: str, intent_file: Path | None = None, plugin_dir: Path = PLUGIN_DIR,
    extra_file: Path | None = None,
) -> list[str]:
    # The input files live outside the worktree, so the reviewers need read access to their folders.
    folders = dict.fromkeys(str(f.parent) for f in (intent_file, extra_file) if f)
    add_dir = [arg for folder in folders for arg in ("--add-dir", folder)]
    return [
        claude, "-p", prompt_for(spec, intent_file, extra_file),
        *add_dir,
        "--model", spec.model,
        "--effort", spec.effort,
        "--output-format", "stream-json", "--verbose",
        "--plugin-dir", str(plugin_dir),
        # Ignore a benchmark repo's own .claude/settings.json (hooks) and .mcp.json.
        "--setting-sources", "user",
        "--strict-mcp-config",
        "--tools", ",".join(TOOLS),
        "--allowedTools", *ALLOWED_TOOLS,
        "--disallowedTools", *DISALLOWED_TOOLS,
        "--permission-mode", "dontAsk",
        "--no-session-persistence",
    ]


def skill_hashes() -> dict[str, str]:
    files = {name: PLUGIN_DIR / rel for name, rel in SKILL_FILES.items()}
    files |= {f"member:{p.stem}": p for p in sorted((PLUGIN_DIR / "skills/review/members").glob("*.md"))}
    return {name: hashlib.sha256(path.read_bytes()).hexdigest()[:12] for name, path in files.items()}


def config_hash(spec: RunSpec, version: str, hashes: dict[str, str]) -> str:
    """Everything besides the member and its file that changes a member's labeled findings."""
    key = {
        "effort": spec.effort,
        "claude_code_version": version,
        **{name: hashes[name] for name in SKILL_FILES},
    }
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:12]


def plugin_tree_hash(root: Path = PLUGIN_DIR) -> str:
    """Hash of every file in the plugin, paths included."""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()[:12]


def plugin_snapshot(work: Path) -> Path:
    """A frozen copy of the plugin outside this repository, keyed by the hash of all its files.

    Runs load the copy, so no path a reviewer sees points into the repository that holds the
    golden comments, and an edit to the plugin can't change runs halfway through a batch.
    """
    dest = work.parent / "plugin" / plugin_tree_hash()
    if not dest.exists():
        shutil.copytree(PLUGIN_DIR, dest)
    return dest


def clean_env(config_dir: Path) -> dict[str, str]:
    """This process's environment without inherited Claude and Anthropic settings, and without
    paths into this repository (uv sets VIRTUAL_ENV and puts the venv on PATH)."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is set; benchmark runs must use the plan (SETUP step 2)")
    env = {
        k: v
        for k, v in os.environ.items()
        if not (
            k.startswith("ANTHROPIC_")
            or k == "CLAUDECODE"
            or (k.startswith("CLAUDE_CODE_") and k != "CLAUDE_CODE_GIT_BASH_PATH")
            or k in {"VIRTUAL_ENV", "PWD", "OLDPWD"}
        )
    }
    root = os.path.normcase(str(ROOT))
    path_key = next((k for k in env if k.upper() == "PATH"), "PATH")
    env[path_key] = os.pathsep.join(
        p
        for p in env.get(path_key, "").split(os.pathsep)
        if not (os.path.normcase(os.path.abspath(p)) + os.sep).startswith(root + os.sep)
    )
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    env["DISABLE_AUTOUPDATER"] = "1"
    # Without Git Bash, Claude Code on Windows offers PowerShell instead of Bash, so the board's
    # agents (tools: Bash) would have no shell and runs would differ from macOS and Linux.
    if os.name == "nt" and "CLAUDE_CODE_GIT_BASH_PATH" not in env and (bash := _windows_git_bash()):
        env["CLAUDE_CODE_GIT_BASH_PATH"] = str(bash)
    return env


def _windows_git_bash() -> Path | None:
    """bash.exe of the Git for Windows that provides `git`."""
    exec_path = Path(gitrepo.git("--exec-path").strip())  # <git root>/mingw64/libexec/git-core
    for root in exec_path.parents:
        if (root / "bin" / "bash.exe").exists():
            return root / "bin" / "bash.exe"
    return None


def check_claude(paths: Paths) -> str:
    """The account letter (A or B), after checking the pinned version and the clean config login."""
    env = clean_env(paths.config_dir)
    # Decode as UTF-8, not the system locale: on Chinese Windows that is GBK, and the org name
    # in `auth status` can be non-ASCII.
    utf8 = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace", "env": env}
    version = subprocess.run([paths.claude, "--version"], **utf8).stdout
    if not version.startswith(PINNED_VERSION):
        raise RuntimeError(f"{paths.claude} is {version.strip()!r}, expected {PINNED_VERSION}")
    status = subprocess.run([paths.claude, "auth", "status"], **utf8)
    auth = json.loads(status.stdout or "{}")
    if not auth.get("loggedIn") or auth.get("apiKeySource"):
        raise RuntimeError(f"{paths.config_dir} is not logged in to a plan")
    account = _ACCOUNTS.get(auth.get("subscriptionType", ""))
    if account is None:
        raise RuntimeError(f"unexpected subscription {auth.get('subscriptionType')!r}")
    return account


def _dataset_commit() -> str:
    return gitrepo.git("-C", str(ROOT), "describe", "--always", "--dirty", "--abbrev=12").strip()


def run_one(spec: RunSpec, paths: Paths, account: str) -> RunMeta:
    out = run_dir(paths, spec)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    run_work = paths.work / spec.dataset / spec.variant / spec.pr_id
    if spec.variant == BOARD_CR:
        meta = _run_board_cr(spec, paths, account, out, run_work)
    else:
        meta = _session(spec, paths, account, out, run_work)
        if meta.status == "ok" and spec.variant.startswith("board-"):
            _write_cache(paths.cache, spec, meta, json.loads((out / "final.json").read_text(encoding="utf-8")))
    (out / "meta.json").write_text(meta.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return meta


def _run_board_cr(spec: RunSpec, paths: Paths, account: str, out: Path, run_work: Path) -> RunMeta:
    """`/code-review` in its own session, then the full board with its findings as an extra member.

    No cache files: the cache path doesn't name the variant, so they would replace a board-full
    run's files, and these members' labels were made with a 13th member present.
    """
    review_out = out / "code-review"
    review_out.mkdir()
    review_spec = spec.model_copy(update={"variant": "code-review"})
    review = _session(review_spec, paths, account, review_out, run_work / "code-review")
    (review_out / "meta.json").write_text(review.model_dump_json(indent=2) + "\n", encoding="utf-8")
    if review.status != "ok":
        if review.status == "failed:usage-limit":
            status, detail = review.status, review.detail
        else:
            status, detail = "failed:code-review", f"{review.status}: {review.detail or ''}"
        meta = review.model_copy(update={"variant": spec.variant, "roster": spec.members, "status": status,
                                         "detail": detail})
        return _add_sessions(meta, {"code-review": review})

    # A finished /code-review session whose reply lists no findings still leaves the board to run, as
    # it would in use: the extra member is then empty (Gate 3, ghost-7, decided 2026-11-06). The flag
    # `parsed: false` marks it, since a reply in a format the parser misses looks the same.
    extra = code_review_member_output(review_out)
    parsed = extra is not None
    if extra is None:
        extra = {"member": EXTRA_MEMBER, "findings": []}
    text = json.dumps(extra, ensure_ascii=False, indent=1) + "\n"
    board = _session(spec, paths, account, out, run_work, extra_findings=text)
    board.started_at = review.started_at
    board.extra_findings = {"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
                            "findings": len(extra["findings"]), "parsed": parsed}
    return _add_sessions(board, {"code-review": review, "board": board})


def _add_sessions(meta: RunMeta, sessions: dict[str, RunMeta]) -> RunMeta:
    """Record each session, and make the run's cost, time, and denials their sums."""
    meta.sessions = {
        name: {"status": s.status, "total_cost_usd": s.total_cost_usd, "wall_seconds": s.wall_seconds,
               "permission_denials": s.permission_denials}
        for name, s in sessions.items()
    }
    costs = [s.total_cost_usd for s in sessions.values() if s.total_cost_usd is not None]
    meta.total_cost_usd = round(sum(costs), 6) if costs else None
    meta.wall_seconds = round(sum(s.wall_seconds or 0 for s in sessions.values()), 1)
    meta.permission_denials = sum(s.permission_denials for s in sessions.values())
    return meta


def _session(
    spec: RunSpec, paths: Paths, account: str, out: Path, run_work: Path, extra_findings: str | None = None
) -> RunMeta:
    """One `claude -p` session: its files go to `out`, its worktree and input files under `run_work`."""
    hashes = skill_hashes()
    inputs = run_work / f"r{spec.repeat}-intent"
    intent_file = inputs / "intent.txt" if spec.intent and spec.variant.startswith("board-") else None
    extra_file = inputs / "extra-findings.json" if extra_findings is not None else None
    if intent_file or extra_file:
        inputs.mkdir(parents=True, exist_ok=True)
    if intent_file:
        intent_file.write_text(spec.intent, encoding="utf-8", newline="\n")
    if extra_file:
        extra_file.write_text(extra_findings, encoding="utf-8", newline="\n")
    command = command_for(spec, paths.claude, intent_file, plugin_snapshot(paths.work), extra_file)
    meta = RunMeta(
        dataset=spec.dataset,
        dataset_commit=_dataset_commit(),
        pr_id=spec.pr_id,
        variant=spec.variant,
        repeat=spec.repeat,
        roster=spec.members if spec.variant.startswith("board-") else None,
        base=spec.base,
        head=spec.head,
        reviewer_model=spec.model,
        effort=spec.effort,
        intent="PR title and description" if spec.intent else "commit messages",
        skill_hashes=hashes,
        config_hash=config_hash(spec, PINNED_VERSION, hashes),
        account=account,
        command=command[1:],
        started_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    )
    worktree = run_work / f"r{spec.repeat}"
    started = time.monotonic()
    _add_worktree(spec.repo, worktree, spec.head)
    try:
        init, result = _stream(command, worktree, paths.config_dir, out, spec)
    finally:
        _remove_worktree(spec.repo, worktree)
        if intent_file or extra_file:
            shutil.rmtree(inputs, ignore_errors=True)
    meta.wall_seconds = round(time.monotonic() - started, 1)
    meta.setup = init
    meta.claude_code_version = init.version if init else None
    _finish(meta, init, result, out, spec)
    return meta


def _add_worktree(repo: Path, worktree: Path, head: str) -> None:
    gitrepo.git("-C", str(repo), "config", "core.autocrlf", "false")
    gitrepo.git("-C", str(repo), "config", "core.longpaths", "true")
    if worktree.exists():
        _remove_worktree(repo, worktree)
    worktree.parent.mkdir(parents=True, exist_ok=True)
    gitrepo.git("-C", str(repo), "worktree", "add", "--quiet", "--detach", str(worktree), head)


def _remove_worktree(repo: Path, worktree: Path) -> None:
    gitrepo.git("-C", str(repo), "worktree", "remove", "--force", str(worktree), check=False)
    shutil.rmtree(worktree, ignore_errors=True)
    gitrepo.git("-C", str(repo), "worktree", "prune", check=False)


def _stream(
    command: list[str], cwd: Path, config_dir: Path, out: Path, spec: RunSpec
) -> tuple[InitInfo | None, ResultInfo | None]:
    expected = ExpectedSetup(version=PINNED_VERSION, model=spec.model, tools=TOOLS)
    init = result = None
    with (out / "stream.jsonl").open("w", encoding="utf-8", newline="\n") as log, (out / "stderr.txt").open(
        "w", encoding="utf-8"
    ) as err:
        proc = subprocess.Popen(
            command, cwd=cwd, env=clean_env(config_dir), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=err, text=True, encoding="utf-8", errors="replace",
        )
        timer = threading.Timer(RUN_TIMEOUT_S, proc.kill)
        timer.start()
        try:
            for line in proc.stdout:
                log.write(line)
                event = _json_or_none(line)
                if not event:
                    continue
                if event.get("type") == "system" and event.get("subtype") == "init":
                    init = InitInfo.from_event(event)
                    if setup_problems(init, expected):
                        proc.kill()
                        break
                elif event.get("type") == "result":
                    result = ResultInfo.from_event(event)
            proc.wait()
        finally:
            timer.cancel()
    return init, result


def _json_or_none(line: str) -> dict[str, Any] | None:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def _finish(meta: RunMeta, init: InitInfo | None, result: ResultInfo | None, out: Path, spec: RunSpec) -> None:
    expected = ExpectedSetup(version=PINNED_VERSION, model=spec.model, tools=TOOLS)
    if init is None:
        meta.status, meta.detail = "failed:no-init", "the run printed no init event; see stderr.txt"
        return
    problems = setup_problems(init, expected)
    if problems:
        meta.status, meta.detail = "failed:setup-mismatch", "; ".join(problems)
        return
    if result is None:
        meta.status, meta.detail = "failed:no-result", "the run ended without a result event; see stderr.txt"
        return
    meta.total_cost_usd = result.total_cost_usd
    meta.model_usage = result.model_usage
    meta.subagents = result.subagents
    meta.num_turns = result.num_turns
    meta.permission_denials = result.permission_denials
    if result.usage_limit:
        meta.status, meta.detail = "failed:usage-limit", result.text[:300]
        return
    if result.is_error:
        # An API error can still report subtype "success"; terminal_reason says what happened.
        reason = result.terminal_reason or (result.subtype if result.subtype != "success" else "") or "error"
        meta.status, meta.detail = f"failed:{reason}", result.text[:300]
        return
    if spec.variant == "code-review":
        (out / "final.md").write_text(result.text, encoding="utf-8", newline="\n")
        meta.status = "ok"
        return
    try:
        board, repair = parse_board_json(result.text)
    except ValueError as e:
        (out / "final.raw.txt").write_text(result.text, encoding="utf-8", newline="\n")
        meta.status, meta.detail = "failed:bad-json", str(e)[:300]
        return
    (out / "final.json").write_text(json.dumps(board, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    meta.parse_repair = repair
    meta.members = board.get("members")
    meta.status = "ok"


def _write_cache(cache: Path, spec: RunSpec, meta: RunMeta, board: dict[str, Any]) -> None:
    """Split the board's findings by member (PLAN 6.2), one file per member at `cache_file`."""
    members = board.get("members") or {}
    entries: dict[str, dict[str, Any]] = {m: {"status": "ran", "findings": []} for m in members.get("ran", [])}
    for skipped in members.get("skipped", []):
        entries[skipped["id"]] = {"status": "skipped", "reason": skipped.get("reason", ""), "findings": []}
    for finding in board.get("findings", []):
        entries.setdefault(finding["member"], {"status": "ran", "findings": []})["findings"].append(finding)
    if "intern" in entries and board.get("explanations"):
        entries["intern"]["explanations"] = board["explanations"]

    for member, entry in entries.items():
        member_hash = meta.skill_hashes.get(f"member:{member}", "unknown")
        path = cache_file(cache, spec, member, meta.config_hash, member_hash)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"member": member, **entry, "source_run": run_name(spec)}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
