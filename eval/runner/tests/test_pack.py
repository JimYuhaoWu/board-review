from __future__ import annotations

import json
import zipfile
from pathlib import Path

from eval.runner.pack import pack_runs


def _run(results: Path, name: str, status: str, **meta: object) -> None:
    run = results / name
    run.mkdir(parents=True)
    (run / "meta.json").write_text(json.dumps({"status": status, **meta}), encoding="utf-8")
    (run / "stream.jsonl").write_text("{}\n", encoding="utf-8")


def test_pack_zips_finished_runs_at_repo_relative_paths(tmp_path: Path):
    results = tmp_path / "results"
    _run(results, "martian/board-full/grafana-1/r1", "ok")
    _run(results, "martian/code-review/grafana-1/r1", "failed:usage-limit")
    out = tmp_path / "packs" / "pilot.zip"
    runs = [("martian", v, "grafana-1", 1) for v in ("board-full", "code-review")] + [("martian", "board-full", "x-2", 1)]

    packed, skipped = pack_runs(results, tmp_path / "cache", runs, out)

    assert packed == ["martian/board-full/grafana-1/r1"]
    assert skipped == ["martian/code-review/grafana-1/r1 (failed:usage-limit)", "martian/board-full/x-2/r1 (missing)"]
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read("eval/results/manifest.json"))
    assert {"eval/results/martian/board-full/grafana-1/r1/meta.json",
            "eval/results/martian/board-full/grafana-1/r1/stream.jsonl"} <= names
    assert not any("code-review" in n for n in names)
    assert manifest["runs"] == packed and len(manifest["skipped"]) == 2


def test_pack_adds_the_cache_files_the_run_wrote(tmp_path: Path):
    results, cache = tmp_path / "results", tmp_path / "cache"
    _run(results, "martian/board-edge/grafana-1/r1", "ok", config_hash="c1", skill_hashes={"member:edge": "edited"})
    member = cache / "martian" / "grafana-1" / "r1" / "edge@m" / "c1"
    member.mkdir(parents=True)
    for name, source in (("edited.json", "martian/board-edge/grafana-1/r1"), ("old.json", "martian/board-full/grafana-1/r1")):
        (member / name).write_text(json.dumps({"member": "edge", "source_run": source}), encoding="utf-8")
    out = tmp_path / "packs" / "edge.zip"

    pack_runs(results, cache, [("martian", "board-edge", "grafana-1", 1)], out)

    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    assert "eval/cache/martian/grafana-1/r1/edge@m/c1/edited.json" in names
    assert not any(n.endswith("old.json") for n in names)


def test_pack_leaves_out_cache_files_of_older_runs_with_the_same_name(tmp_path: Path):
    """A run moved aside and run again keeps its name, so its old cache files still say they
    are from this run. Only the files at this run's config and member hashes are its own."""
    results, cache = tmp_path / "results", tmp_path / "cache"
    name = "martian/board-full/grafana-1/r1"
    _run(results, name, "ok", config_hash="new", skill_hashes={"member:edge": "e2", "member:line-reader": "l1"})
    run_cache = cache / "martian" / "grafana-1" / "r1"
    for rel in ("edge@m/new/e2.json", "line-reader@m/new/l1.json", "edge@m/new/e1.json", "edge@m/old/e1.json"):
        (run_cache / rel).parent.mkdir(parents=True, exist_ok=True)
        (run_cache / rel).write_text(json.dumps({"source_run": name}), encoding="utf-8")
    out = tmp_path / "packs" / "pilot.zip"

    pack_runs(results, cache, [("martian", "board-full", "grafana-1", 1)], out)

    with zipfile.ZipFile(out) as zf:
        cached = sorted(n.removeprefix("eval/cache/martian/grafana-1/r1/") for n in zf.namelist() if n.startswith("eval/cache"))
    assert cached == ["edge@m/new/e2.json", "line-reader@m/new/l1.json"]
