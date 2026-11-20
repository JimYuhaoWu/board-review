from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from eval.runner import __main__ as cli
from eval.runner import revise
from eval.runner import run as runner

CLEAN, BUGGY = "x = 1\nlimit = 10\n", "x = 1\nlimit = 0\n"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def pr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str, str, str]:
    """A bare PR repository like `prs/<id>.git`: base, the clean PR, and head, which plants a bug."""
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(var, "t")
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(var, "t@example.invalid")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    src = tmp_path / "src"
    src.mkdir()
    _git(src, "init", "-q", "-b", "main")
    _git(src, "config", "core.autocrlf", "false")
    (src / "a.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
    (src / "b.txt").write_bytes(b"one\r\ntwo\r\n")
    _git(src, "add", ".")
    _git(src, "commit", "-q", "-m", "base")
    base = _git(src, "rev-parse", "HEAD")
    (src / "a.py").write_text(CLEAN, encoding="utf-8", newline="\n")
    _git(src, "commit", "-q", "-am", "the PR")
    clean = _git(src, "rev-parse", "HEAD")
    (src / "a.py").write_text(BUGGY, encoding="utf-8", newline="\n")
    _git(src, "commit", "-q", "-am", "update pr")
    head = _git(src, "rev-parse", "HEAD")
    repo = tmp_path / "pr.git"
    _git(tmp_path, "clone", "-q", "--bare", str(src), str(repo))
    return repo, base, clean, head


def test_the_clone_holds_base_to_head_as_one_commit_without_the_clean_one(tmp_path: Path, pr):
    repo, base, clean, head = pr
    clone = tmp_path / "clone"
    revise.make_clone(repo, base, head, clone)
    assert subprocess.run(["git", "cat-file", "-e", clean], cwd=clone, capture_output=True).returncode != 0
    assert _git(clone, "rev-parse", "HEAD~1") == base
    assert _git(clone, "rev-parse", "HEAD^{tree}") == _git(repo, "rev-parse", f"{head}^{{tree}}")
    assert _git(clone, "branch", "--show-current") == "pr"
    assert _git(clone, "remote") == "" and _git(clone, "reflog") == ""
    assert _git(repo, "for-each-ref", "--format=%(refname)") == "refs/heads/main", "no temporary branch left"


def test_diffs_hold_new_files_and_line_endings_and_vs_clean_starts_from_head_parent(tmp_path: Path, pr):
    repo, base, clean, head = pr
    clone = tmp_path / "clone"
    revise.make_clone(repo, base, head, clone)
    (clone / "a.py").write_bytes(CLEAN.encode())
    (clone / "b.txt").write_bytes(b"one\r\nTWO\r\n")
    (clone / "c.py").write_bytes(b"new\n")
    revision = revise.revision_diff(clone)
    assert b"+limit = 10" in revision and b"+TWO\r\n" in revision and b"c.py" in revision
    vs_clean = revise.vs_clean_diff(repo, head, revision, tmp_path / "scratch")
    assert b"a.py" not in vs_clean, "the fix restored the clean file"
    assert b"b.txt" in vs_clean and b"c.py" in vs_clean
    untouched = revise.vs_clean_diff(repo, head, b"", tmp_path / "scratch2")
    assert untouched == subprocess.run(["git", "diff", "--binary", clean, head], cwd=repo, capture_output=True).stdout


def _findings_file(path: Path, **overrides) -> Path:
    data = {
        "schema_version": 1, "dataset": "smoke", "pr_id": "smoke", "input": "G", "repeat": 1,
        "source": {"golden_sha256": "abc"},
        "findings": [{"id": "F1", "texts": ["`a.py:2`: limit is 0"]}, {"id": "F2", "texts": ["`a.py:1`: x", "x again"]}],
    }
    path.write_text(json.dumps({**data, **overrides}), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"input": "N1"}, "input"),
        ({"repeat": 2}, "repeat"),
        ({"pr_id": "other"}, "pr_id"),
        ({"findings": [{"id": "F2", "texts": ["x"]}]}, "F1, F2"),
        ({"findings": [{"id": "F1", "texts": []}]}, "texts"),
    ],
)
def test_a_findings_file_for_another_run_is_refused(tmp_path: Path, overrides: dict, fragment: str):
    with pytest.raises(ValueError, match=fragment):
        revise.load_findings(_findings_file(tmp_path / "f.json", **overrides), "smoke", "smoke", "G", 1)


def test_the_reviser_reads_ids_and_wordings_only(tmp_path: Path):
    findings = revise.load_findings(_findings_file(tmp_path / "f.json"), "smoke", "smoke", "G", 1)
    assert json.loads(revise.neutral_findings(findings)) == {
        "findings": [{"id": "F1", "texts": ["`a.py:2`: limit is 0"]}, {"id": "F2", "texts": ["`a.py:1`: x", "x again"]}]
    }


def _report(*entries: tuple[str, str]) -> str:
    return json.dumps({"findings": [{"id": i, "action": a, "reason": "r"} for i, a in entries]})


def test_report_needs_one_entry_per_id_with_an_action():
    report, repair = revise.parse_report("```json\n" + _report(("F2", "declined"), ("F1", "fixed")) + "\n```", ["F1", "F2"])
    assert repair == "stripped-fence" and len(report["findings"]) == 2
    for bad in (_report(("F1", "fixed")), _report(("F1", "fixed"), ("F1", "fixed")),
                _report(("F1", "fixed"), ("F2", "skipped")), "no json"):
        with pytest.raises(ValueError):
            revise.parse_report(bad, ["F1", "F2"])


def _spec(tmp_path: Path, repo: Path, base: str, head: str, findings: Path) -> runner.RunSpec:
    return runner.RunSpec(dataset="smoke", pr_id="smoke", variant="revise-G", repeat=1, repo=repo, base=base, head=head,
                          intent="Lower the limit check", findings_file=findings)


def test_the_reviser_command_edits_with_read_only_git_and_no_board(tmp_path: Path):
    findings = tmp_path / "in" / "findings.json"
    spec = _spec(tmp_path, tmp_path, "b" * 40, "h" * 40, findings)
    command = runner.command_for(spec, "claude", tmp_path / "in" / "intent.txt", extra_file=findings)
    assert "--plugin-dir" not in command
    assert command[command.index("--tools") + 1] == "Read,Grep,Glob,Bash,Edit,Write"
    allowed = command[command.index("--allowedTools") + 1 : command.index("--disallowedTools")]
    assert {"Edit", "Write", "Bash(git show *)"} <= set(allowed) and "Task" not in allowed
    assert not any("commit" in a for a in allowed)
    prompt = command[2]
    assert "`HEAD~1..HEAD`" in prompt and findings.as_posix() in prompt and "intent.txt" in prompt
    assert "Edit files with the Edit tool." in prompt
    assert runner.expected_setup(spec).plugin is None


def _init(**overrides) -> dict:
    return {"type": "system", "subtype": "init", "claude_code_version": runner.PINNED_VERSION, "model": runner.MODEL,
            "tools": list(runner.REVISE_TOOLS), "mcp_servers": [],
            "plugins": [{"name": "cc-plugin-telemetry", "source": "cc-plugin-telemetry@builtin"}],
            "skills": ["code-review"], **overrides}


class _FakeReviser:
    """Stands in for `claude -p`: checks what the reviser can see, then edits like one."""

    def __init__(self, command: list[str], cwd: Path, clean: str, reply: str):
        assert subprocess.run(["git", "cat-file", "-e", clean], cwd=cwd, capture_output=True).returncode != 0
        folders = [Path(command[i + 1]) for i, arg in enumerate(command) if arg == "--add-dir"]
        assert sorted(p.name for p in folders[0].iterdir()) == ["findings.json", "intent.txt"]
        assert "revise-G" not in " ".join(command) and "r1" not in str(cwd)
        (cwd / "a.py").write_text(CLEAN, encoding="utf-8", newline="\n")
        result = {"type": "result", "is_error": False, "subtype": "success", "result": reply, "total_cost_usd": 0.2}
        self.stdout = iter(json.dumps(e) + "\n" for e in (_init(), result))

    def kill(self) -> None:
        pass

    def wait(self) -> int:
        return 0


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pr, reply: str) -> tuple[runner.RunMeta, Path]:
    repo, base, clean, head = pr
    real_popen = subprocess.Popen

    def fake_popen(command, *args, **kwargs):
        if command[0] != "claude":
            return real_popen(command, *args, **kwargs)
        return _FakeReviser(command, Path(kwargs["cwd"]), clean, reply)

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    paths = runner.Paths(results=tmp_path / "results", cache=tmp_path / "cache", work=tmp_path / "work",
                         config_dir=tmp_path / "cfg", claude="claude")
    spec = _spec(tmp_path, repo, base, head, _findings_file(tmp_path / "f.json"))
    meta = runner.run_one(spec, paths, "B")
    return meta, runner.run_dir(paths, spec)


def test_revise_run_writes_both_diffs_and_the_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pr):
    meta, out = _run(tmp_path, monkeypatch, pr, _report(("F1", "fixed"), ("F2", "declined")))
    assert meta.status == "ok", meta.detail
    assert out.parent.parent.name == "revise-G"
    assert b"+limit = 10" in (out / "revision.diff").read_bytes()
    assert (out / "vs-clean.diff").read_bytes() == b"", "the revision is the clean commit"
    assert json.loads((out / "report.json").read_text(encoding="utf-8"))["findings"][0]["action"] == "fixed"
    assert b"\r\n" not in (out / "report.json").read_bytes()
    findings = tmp_path / "f.json"
    assert meta.revise["input_sha256"] == hashlib.sha256(findings.read_bytes()).hexdigest()[:12]
    assert meta.revise["input_sha256"] != meta.revise["findings_sha256"]
    assert {k: meta.revise[k] for k in ("input", "prompt_sha256", "findings", "fixed", "declined")} == {
        "input": "G", "prompt_sha256": revise.PROMPT_SHA256, "findings": 2, "fixed": 1, "declined": 1}
    assert not any((tmp_path / "work" / "smoke" / "revise" / "smoke").iterdir()), "the clone and inputs are gone"


def test_a_bad_report_fails_the_run_but_keeps_the_diffs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pr):
    meta, out = _run(tmp_path, monkeypatch, pr, _report(("F1", "fixed")))
    assert meta.status == "failed:bad-report"
    assert (out / "report.raw.txt").exists() and (out / "revision.diff").read_bytes()


def test_revise_needs_an_input_and_a_matching_findings_file(tmp_path: Path, capsys: pytest.CaptureFixture, pr):
    repo, base, clean, head = pr
    common = ["run", "--variant", "revise", "--smoke-repo", str(repo), "--base", base, "--head", head, "--repeat", "1",
              "--dry-run"]
    with pytest.raises(SystemExit, match="needs --input"):
        cli.main(common)
    findings = _findings_file(tmp_path / "f.json")
    cli.main([*common, "--input", "G", "--findings-file", str(findings)])
    out = capsys.readouterr().out
    assert "smoke/revise-G/smoke/r1 (to run)" in out and "HEAD~1..HEAD" in out
    cli.main([*common, "--input", "N1", "--findings-file", str(findings)])
    assert "findings file:" in capsys.readouterr().out
