from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from eval.runner import __main__ as cli
from eval.runner import run as runner
from eval.runner.stream import ExpectedSetup, InitInfo, ResultInfo, parse_board_json, setup_problems
from eval.scoring.data import code_review_member_output

EXPECTED = ExpectedSetup(version="2.1.317", model="claude-sonnet-5-5", tools=runner.TOOLS)


def _init_event(**overrides) -> dict:
    event = {
        "type": "system",
        "subtype": "init",
        "claude_code_version": "2.1.317",
        "model": "claude-sonnet-5-5",
        "tools": list(runner.TOOLS),
        "mcp_servers": [],
        "plugins": [
            {"name": "board-review", "source": "board-review@inline"},
            {"name": "cc-plugin-telemetry", "source": "cc-plugin-telemetry@builtin"},
        ],
        "skills": ["code-review", "debug", "board-review:review"],
    }
    event.update(overrides)
    return event


def test_clean_setup_has_no_problems():
    assert setup_problems(InitInfo.from_event(_init_event()), EXPECTED) == []


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"claude_code_version": "2.1.320"}, "Claude Code 2.1.320"),
        ({"mcp_servers": [{"name": "claude.ai Google Drive", "status": "pending"}]}, "MCP servers"),
        ({"skills": ["code-review", "anthropic-skills:docx", "board-review:review"]}, "anthropic-skills:docx"),
        ({"plugins": [{"name": "x", "source": "cc-plugin-telemetry@builtin"}]}, "not loaded"),
        ({"tools": [*runner.TOOLS, "WebFetch"]}, "tools"),
    ],
)
def test_setup_problems_are_reported(overrides: dict, fragment: str):
    problems = setup_problems(InitInfo.from_event(_init_event(**overrides)), EXPECTED)
    assert any(fragment in p for p in problems)


@pytest.mark.parametrize(
    ("event", "limited"),
    [
        ({"is_error": True, "api_error_status": 429, "result": "API Error"}, True),
        ({"is_error": True, "result": "You've hit your usage limit · resets 5pm"}, True),
        ({"is_error": True, "result": "You've hit your 5-hour limit · resets 5:50pm (Asia/Shanghai)"}, True),
        ({"is_error": False, "result": "You've hit your 5-hour limit · resets 5:50pm (Asia/Shanghai)"}, True),
        ({"is_error": True, "result": "API Error: 500 overloaded"}, False),
        ({"is_error": False, "result": "the limit reached in loop 3 is a bug"}, False),
        ({"is_error": False, "result": "No findings. The retry stops once you've hit your limit of 3 tries."}, False),
    ],
)
def test_usage_limit_detection(event: dict, limited: bool):
    assert ResultInfo.from_event({"type": "result", **event}).usage_limit is limited


@pytest.mark.parametrize(
    ("text", "repair"),
    [
        ('{"schema_version": 1}', "none"),
        ('```json\n{"schema_version": 1}\n```', "stripped-fence"),
        ('"schema_version": 1}', "added-brace"),
        ('Here is the report:\n{"schema_version": 1}\nDone.', "extracted-object"),
    ],
)
def test_board_json_repairs(text: str, repair: str):
    assert parse_board_json(text) == ({"schema_version": 1}, repair)


def test_board_json_failure_raises():
    with pytest.raises(ValueError):
        parse_board_json("no json here")


def _spec(tmp_path: Path, variant: str, repo: Path, base: str, head: str) -> runner.RunSpec:
    return runner.RunSpec(dataset="smoke", pr_id="smoke", variant=variant, repeat=1, repo=repo, base=base, head=head)


def test_prompts_pin_the_variant_commands(tmp_path: Path):
    a = _spec(tmp_path, "code-review", tmp_path, "b" * 40, "h" * 40)
    c = a.model_copy(update={"variant": "board-full"})
    target = f"{'b' * 40}..{'h' * 40}"
    assert runner.prompt_for(a, None) == f"/code-review medium --max-findings all {target}"
    assert runner.prompt_for(c, None) == f"/board-review:review --members full --max-findings 8 --format json {target}"
    command = runner.command_for(c, "claude")
    for flag in ("--setting-sources", "--strict-mcp-config", "--no-session-persistence"):
        assert flag in command
    assert "--add-dir" not in command
    assert command[command.index("--permission-mode") + 1] == "dontAsk"


def test_both_variants_get_the_intent(tmp_path: Path):
    intent = "Add search\n\nPages start at 1. Run `pytest --maxfail=1`."
    a = _spec(tmp_path, "code-review", tmp_path, "b" * 40, "h" * 40).model_copy(update={"intent": intent})
    c = a.model_copy(update={"variant": "board-full"})
    intent_file = tmp_path / "r1-intent" / "intent.txt"
    assert runner.prompt_for(a, None).endswith(f"{'h' * 40}\n\n{intent}")
    assert f"--intent-file {intent_file.as_posix()} {'b' * 40}.." in runner.prompt_for(c, intent_file)
    command = runner.command_for(c, "claude", intent_file)
    assert command[command.index("--add-dir") + 1] == str(intent_file.parent)


def test_clean_env_drops_inherited_claude_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "desktop")
    monkeypatch.setenv("CLAUDE_CODE_GIT_BASH_PATH", "C:/git/bash.exe")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://local")
    venv_bin = str(runner.ROOT / ".venv" / "Scripts")
    sibling = str(runner.ROOT) + "-data"
    monkeypatch.setenv("VIRTUAL_ENV", str(runner.ROOT / ".venv"))
    monkeypatch.setenv("PATH", runner.os.pathsep.join([venv_bin, sibling, "/usr/bin"]))
    env = runner.clean_env(tmp_path)
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path) and env["DISABLE_AUTOUPDATER"] == "1"
    assert "CLAUDECODE" not in env and "CLAUDE_CODE_ENTRYPOINT" not in env and "ANTHROPIC_BASE_URL" not in env
    assert env["CLAUDE_CODE_GIT_BASH_PATH"] == "C:/git/bash.exe"
    assert "VIRTUAL_ENV" not in env
    path = next(v for k, v in env.items() if k.upper() == "PATH").split(runner.os.pathsep)
    assert venv_bin not in path and sibling in path, "only paths inside the repository are dropped"


def test_plugin_hash_covers_every_file(tmp_path: Path):
    root = tmp_path / "plugin"
    (root / ".claude-plugin").mkdir(parents=True)
    manifest = root / ".claude-plugin" / "plugin.json"
    manifest.write_text('{"name": "board-review"}', encoding="utf-8")
    before = runner.plugin_tree_hash(root)
    manifest.write_text('{"name": "board-review", "version": "0.2.0"}', encoding="utf-8")
    assert runner.plugin_tree_hash(root) != before


@pytest.mark.skipif(runner.os.name != "nt", reason="Windows only")
def test_clean_env_points_claude_at_git_bash_on_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_GIT_BASH_PATH", raising=False)
    bash = Path(runner.clean_env(tmp_path)["CLAUDE_CODE_GIT_BASH_PATH"])
    assert bash.name == "bash.exe" and bash.exists()


def test_api_key_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        runner.clean_env(tmp_path)


class _FakeProcess:
    """Stands in for `claude -p`: prints canned stream-json lines."""

    def __init__(self, lines: list[dict]):
        self.stdout = iter(json.dumps(line) + "\n" for line in lines)
        self.killed = False

    def kill(self) -> None:
        self.killed = True

    def wait(self) -> int:
        return 0


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str, str]:
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(var, "t")
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(var, "t@example.invalid")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    (path / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(path, "add", "a.py")
    _git(path, "commit", "-q", "-m", "base")
    base = _git(path, "rev-parse", "HEAD")
    (path / "a.py").write_text("x = 2\n", encoding="utf-8")
    _git(path, "commit", "-q", "-am", "head")
    return path, base, _git(path, "rev-parse", "HEAD")


def _paths(tmp_path: Path) -> runner.Paths:
    return runner.Paths(
        results=tmp_path / "results", cache=tmp_path / "cache", work=tmp_path / "work",
        config_dir=tmp_path / "cfg", claude="claude",
    )


def _run_with(monkeypatch: pytest.MonkeyPatch, lines: list[dict]) -> list[_FakeProcess]:
    started: list[_FakeProcess] = []
    real_popen = subprocess.Popen

    def fake_popen(command, *args, **kwargs):
        if command[0] != "claude":
            return real_popen(command, *args, **kwargs)
        assert (Path(kwargs["cwd"]) / "a.py").read_text(encoding="utf-8") == "x = 2\n", "worktree is at the head"
        started.append(_FakeProcess(lines))
        return started[-1]

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    return started


BOARD = {
    "schema_version": 1,
    "members": {"ran": ["edge", "intern"], "skipped": [{"id": "perf", "reason": "no loops"}], "failed": []},
    "findings": [
        {"id": "edge-1", "member": "edge", "group": "G1", "label": "CONFIRMED", "file": "a.py", "line": 1,
         "claim": "x changed", "evidence": "a.py:1", "severity": "Low", "category": "bug", "confidence": 0.9},
    ],
    "explanations": [{"file": "a.py", "unit": "x", "what_it_does": "sets x to 2"}],
}


def test_board_run_writes_layout_and_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo):
    path, base, head = repo
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    usage = {"claude-sonnet-5-5": {"inputTokens": 26, "outputTokens": 7342, "cacheReadInputTokens": 133106}}
    _run_with(monkeypatch, [_init_event(), {"type": "result", "is_error": False, "subtype": "success",
                                             "result": json.dumps(BOARD)[1:], "total_cost_usd": 1.5, "num_turns": 9,
                                             "usage": {"input_tokens": 0}, "modelUsage": usage,
                                             "subagent_stats": {"spawned": 3}}])
    paths = _paths(tmp_path)
    spec = _spec(tmp_path, "board-full", path, base, head).model_copy(update={"intent": "Set x to 2"})
    meta = runner.run_one(spec, paths, "B")

    assert meta.status == "ok", meta.detail
    assert meta.intent == "PR title and description"
    intent_file = paths.work / "smoke" / "board-full" / "smoke" / "r1-intent" / "intent.txt"
    assert f"--intent-file {intent_file.as_posix()}" in meta.command[1]
    assert not intent_file.exists(), "intent folder removed after the run"
    plugin = Path(meta.command[meta.command.index("--plugin-dir") + 1])
    assert plugin.parent == tmp_path / "plugin" and (plugin / "skills" / "review" / "SKILL.md").exists()
    assert str(runner.ROOT) not in " ".join(meta.command), "no path into the repository"
    assert meta.parse_repair == "added-brace"
    assert meta.model_usage == usage and meta.subagents == {"spawned": 3}
    out = runner.run_dir(paths, spec)
    assert {p.name for p in out.iterdir()} == {"stream.jsonl", "stderr.txt", "final.json", "meta.json"}
    assert runner.is_done(paths, spec)
    assert not (paths.work / "smoke" / "board-full" / "smoke" / "r1").exists(), "worktree removed"

    member_dirs = paths.cache / "smoke" / "smoke" / "r1"
    edge = next((member_dirs / "edge@claude-sonnet-5-5" / meta.config_hash).glob("*.json"))
    assert json.loads(edge.read_text(encoding="utf-8"))["findings"][0]["id"] == "edge-1"
    intern = json.loads(next((member_dirs / "intern@claude-sonnet-5-5").rglob("*.json")).read_text(encoding="utf-8"))
    assert intern["findings"] == [] and intern["explanations"][0]["unit"] == "x"
    perf = json.loads(next((member_dirs / "perf@claude-sonnet-5-5").rglob("*.json")).read_text(encoding="utf-8"))
    assert perf["status"] == "skipped"


@pytest.mark.parametrize(
    "stop",
    [
        {"is_error": True, "subtype": "error", "result": "You've hit your usage limit · resets 5pm"},
        # Gate 3: the notice came back as an ordinary reply, and the run was recorded as ok.
        {"is_error": False, "subtype": "success", "result": "You've hit your 5-hour limit · resets 5:50pm (Asia/Shanghai)"},
    ],
)
def test_usage_limit_is_never_an_empty_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo, stop: dict):
    path, base, head = repo
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    _run_with(monkeypatch, [_init_event(), {"type": "result", **stop}])
    paths = _paths(tmp_path)
    spec = _spec(tmp_path, "code-review", path, base, head)
    meta = runner.run_one(spec, paths, "B")
    assert meta.status == "failed:usage-limit"
    assert not (runner.run_dir(paths, spec) / "final.md").exists()
    assert not runner.is_done(paths, spec)


def test_api_error_status_names_the_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo):
    path, base, head = repo
    monkeypatch.chdir(Path(__file__).resolve().parents[3])
    _run_with(monkeypatch, [_init_event(), {"type": "result", "is_error": True, "subtype": "success",
                                             "terminal_reason": "api_error", "result": "issue with the model"}])
    meta = runner.run_one(_spec(tmp_path, "code-review", path, base, head), _paths(tmp_path), "B")
    assert meta.status == "failed:api_error"


def test_setup_mismatch_stops_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo):
    path, base, head = repo
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    started = _run_with(monkeypatch, [_init_event(mcp_servers=[{"name": "claude.ai Drive"}]),
                                      {"type": "result", "is_error": False, "subtype": "success", "result": "x"}])
    meta = runner.run_one(_spec(tmp_path, "code-review", path, base, head), _paths(tmp_path), "B")
    assert meta.status == "failed:setup-mismatch"
    assert started[0].killed


def _board_result(board: dict) -> dict:
    return {"type": "result", "is_error": False, "subtype": "success", "result": json.dumps(board)}


DEV_PR, TEST_PR = "cal_dot_com-7232", "cal_dot_com-22345"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--members", "edge", "--set", "test"], "development PRs only"),
        (["--members", "edge", "--set", "all"], "development PRs only"),
        (["--members", "edge", "--prs", f"{DEV_PR},{TEST_PR}"], "development PRs only"),
        (["--members", "edge,intern", "--set", "dev"], "one member"),
        (["--members", "ghost", "--set", "dev"], "unknown member"),
    ],
)
def test_single_member_runs_are_refused(tmp_path: Path, args: list[str], message: str):
    with pytest.raises(SystemExit, match=message):
        cli.main(["run", "--variant", "board-full", "--repeat", "1", "--dry-run", "--results-dir", str(tmp_path), *args])


def test_single_member_runs_get_their_own_variant(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    cli.main(["run", "--variant", "board-full", "--members", "edge", "--prs", DEV_PR, "--repeat", "1", "--dry-run",
              "--results-dir", str(tmp_path / "results"), "--cache-dir", str(tmp_path / "cache")])
    out = capsys.readouterr().out
    assert f"martian/board-edge/{DEV_PR}/r1 (to run)" in out
    assert "--members edge --max-findings 8" in out


def test_skipped_member_is_an_ok_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo):
    path, base, head = repo
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    board = {"schema_version": 1, "findings": [],
             "members": {"ran": [], "skipped": [{"id": "concurrency", "reason": "no shared state"}], "failed": []}}
    _run_with(monkeypatch, [_init_event(), _board_result(board)])
    paths = _paths(tmp_path)
    spec = _spec(tmp_path, "board-concurrency", path, base, head).model_copy(update={"members": "concurrency"})
    meta = runner.run_one(spec, paths, "B")

    assert meta.status == "ok" and meta.roster == "concurrency"
    assert "--members concurrency " in meta.command[1]
    cached = runner.cache_file(paths.cache, spec, "concurrency", meta.config_hash, meta.skill_hashes["member:concurrency"])
    entry = json.loads(cached.read_text(encoding="utf-8"))
    assert entry["status"] == "skipped" and entry["source_run"] == "smoke/board-concurrency/smoke/r1"


def test_stale_board_runs_name_what_changed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo):
    path, base, head = repo
    _run_with(monkeypatch, [_init_event(), _board_result(BOARD)])
    paths = _paths(tmp_path)
    spec = _spec(tmp_path, "board-full", path, base, head)
    runner.run_one(spec, paths, "B")
    assert runner.stale_reason(paths, spec) is None

    current = runner.skill_hashes()
    # BOARD ran edge and intern and skipped perf; the saboteur didn't take part, so its edit doesn't matter.
    monkeypatch.setattr(runner, "skill_hashes", lambda: {**current, "member:perf": "new", "member:saboteur": "new"})
    assert runner.stale_reason(paths, spec) == "member perf"
    monkeypatch.setattr(runner, "skill_hashes", lambda: {**current, "verifier": "new"})
    assert runner.stale_reason(paths, spec) == "verifier"
    assert runner.stale_reason(paths, spec.model_copy(update={"variant": "code-review"})) is None


def test_run_stops_on_stale_runs_before_starting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo):
    path, base, head = repo
    old = tmp_path / "results" / "smoke" / "board-full" / "smoke" / "r1"
    old.mkdir(parents=True)
    meta = {"status": "ok", "skill_hashes": {}, "config_hash": "old", "members": {"ran": ["edge"]}}
    (old / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr(cli, "check_claude", lambda paths: pytest.fail("a run started"))
    with pytest.raises(SystemExit, match="older plugin"):
        cli.main(["run", "--variant", "board-full", "--smoke-repo", str(path), "--base", base, "--head", head,
                  "--repeat", "1", "--results-dir", str(tmp_path / "results"), "--cache-dir", str(tmp_path / "cache"),
                  "--data-dir", str(tmp_path)])


def test_unchanged_member_alone_would_overwrite_the_full_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo):
    path, base, head = repo
    _run_with(monkeypatch, [_init_event(), _board_result(BOARD)])
    paths = _paths(tmp_path)
    full = _spec(tmp_path, "board-full", path, base, head)
    runner.run_one(full, paths, "B")
    alone = full.model_copy(update={"variant": "board-edge", "members": "edge"})
    assert runner.cache_clash(paths, alone) == "smoke/board-full/smoke/r1"

    current = runner.skill_hashes()
    monkeypatch.setattr(runner, "skill_hashes", lambda: {**current, "member:edge": "edited"})
    assert runner.cache_clash(paths, alone) is None


def _run_sessions(monkeypatch: pytest.MonkeyPatch, *sessions: list[dict]) -> list[_FakeProcess]:
    """Like _run_with, but the n-th `claude` call prints the n-th session. Each process keeps its
    command and the files its --add-dir folders held when it started."""
    started: list[_FakeProcess] = []
    real_popen = subprocess.Popen
    queue = list(sessions)

    def fake_popen(command, *args, **kwargs):
        if command[0] != "claude":
            return real_popen(command, *args, **kwargs)
        process = _FakeProcess(queue.pop(0))
        process.command = command
        folders = [Path(command[i + 1]) for i, arg in enumerate(command) if arg == "--add-dir"]
        process.inputs = {p.name: p.read_text(encoding="utf-8") for folder in folders for p in folder.iterdir()}
        started.append(process)
        return process

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    return started


REVIEW_TEXT = "1. `a.py:1`: x is now 2, but callers expect 1\n   Failure scenario: a caller reads x and gets 2.\n"
USAGE_LIMIT = {"type": "result", "is_error": True, "subtype": "error", "result": "You've hit your usage limit · resets 5pm"}


def _review_result(text: str = REVIEW_TEXT, **fields) -> dict:
    return {"type": "result", "is_error": False, "subtype": "success", "result": text, **fields}


def test_board_cr_runs_code_review_then_the_board_with_its_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo
):
    path, base, head = repo
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    board = {**BOARD, "members": {**BOARD["members"], "ran": ["edge", "intern", "code-review"]}}
    started = _run_sessions(
        monkeypatch,
        [_init_event(), _review_result(total_cost_usd=0.1, permission_denials=[{"tool_name": "Bash"}])],
        [_init_event(), {**_board_result(board), "total_cost_usd": 1.5}],
    )
    paths = _paths(tmp_path)
    spec = _spec(tmp_path, "board-cr", path, base, head).model_copy(update={"intent": "Set x to 2"})
    meta = runner.run_one(spec, paths, "B")

    assert meta.status == "ok", meta.detail
    review = runner.run_dir(paths, spec) / "code-review"
    assert {p.name for p in review.iterdir()} == {"stream.jsonl", "stderr.txt", "final.md", "meta.json"}
    assert json.loads((review / "meta.json").read_text(encoding="utf-8"))["variant"] == "code-review"
    assert started[0].command[2].startswith(f"/code-review medium --max-findings all {base}..{head}")

    inputs = paths.work / "smoke" / "board-cr" / "smoke" / "r1-intent"
    board_command = started[1].command
    assert (f"--intent-file {(inputs / 'intent.txt').as_posix()} "
            f"--extra-findings {(inputs / 'extra-findings.json').as_posix()} {base}..{head}") in board_command[2]
    assert board_command.count("--add-dir") == 1 and board_command[board_command.index("--add-dir") + 1] == str(inputs)
    extra = started[1].inputs["extra-findings.json"]
    assert json.loads(extra) == code_review_member_output(review)
    assert json.loads(extra)["findings"][0]["file"] == "a.py"
    assert not inputs.exists(), "input folder removed after the run"

    assert meta.extra_findings == {"sha256": hashlib.sha256(extra.encode("utf-8")).hexdigest()[:12], "findings": 1, "parsed": True}
    assert meta.sessions["code-review"]["total_cost_usd"] == 0.1 and meta.sessions["board"]["total_cost_usd"] == 1.5
    assert meta.total_cost_usd == pytest.approx(1.6) and meta.permission_denials == 1
    assert meta.members["ran"][-1] == "code-review"
    assert runner.is_done(paths, spec)
    assert not paths.cache.exists(), "board-cr writes no cache files"


def test_board_cr_without_review_findings_runs_the_board_with_an_empty_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo
):
    path, base, head = repo
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    started = _run_sessions(
        monkeypatch,
        [_init_event(), _review_result("No issues found.", total_cost_usd=0.05)],
        [_init_event(), {**_board_result(BOARD), "total_cost_usd": 1.5}],
    )
    paths = _paths(tmp_path)
    spec = _spec(tmp_path, "board-cr", path, base, head)
    meta = runner.run_one(spec, paths, "B")

    assert meta.status == "ok", meta.detail
    assert len(started) == 2, "the board ran"
    assert json.loads(started[1].inputs["extra-findings.json"]) == {"member": "code-review", "findings": []}
    assert meta.extra_findings["findings"] == 0 and meta.extra_findings["parsed"] is False
    assert meta.total_cost_usd == pytest.approx(1.55) and list(meta.sessions) == ["code-review", "board"]
    assert runner.is_done(paths, spec)


def test_board_cr_failed_review_never_starts_the_board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo):
    path, base, head = repo
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    failed = {"type": "result", "is_error": True, "subtype": "error_during_execution", "result": "boom"}
    started = _run_sessions(monkeypatch, [_init_event(), failed])
    paths = _paths(tmp_path)
    spec = _spec(tmp_path, "board-cr", path, base, head)
    meta = runner.run_one(spec, paths, "B")

    assert meta.status == "failed:code-review"
    assert len(started) == 1, "the board never started"
    assert (runner.run_dir(paths, spec) / "code-review" / "meta.json").exists()
    assert list(meta.sessions) == ["code-review"]
    assert not runner.is_done(paths, spec)


@pytest.mark.parametrize("limited", ["code-review", "board"])
def test_board_cr_usage_stop_in_either_session_redoes_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo, limited: str
):
    path, base, head = repo
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    sessions = [[_init_event(), USAGE_LIMIT]]
    if limited == "board":
        sessions = [[_init_event(), _review_result()], [_init_event(), USAGE_LIMIT]]
    started = _run_sessions(monkeypatch, *sessions)
    paths = _paths(tmp_path)
    spec = _spec(tmp_path, "board-cr", path, base, head)
    assert runner.run_one(spec, paths, "B").status == "failed:usage-limit"
    assert len(started) == len(sessions) and not runner.is_done(paths, spec)

    started = _run_sessions(monkeypatch, [_init_event(), _review_result()], [_init_event(), _board_result(BOARD)])
    assert runner.run_one(spec, paths, "B").status == "ok"
    assert len(started) == 2, "the retry redid both sessions"


def test_board_cr_runs_the_full_board(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    common = ["run", "--variant", "board-cr", "--prs", DEV_PR, "--repeat", "1", "--dry-run",
              "--results-dir", str(tmp_path)]
    with pytest.raises(SystemExit, match="--members must be full"):
        cli.main([*common, "--members", "edge"])
    cli.main(common)
    out = capsys.readouterr().out
    assert f"martian/board-cr/{DEV_PR}/r1 (to run)" in out
    assert "/code-review medium --max-findings all" in out
    assert "--intent-file <intent-file> --extra-findings <extra-findings-file>" in out
