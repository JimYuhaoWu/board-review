from __future__ import annotations

import json
import zipfile
from pathlib import Path

from eval.runner.pack import pack_runs


def _run(results: Path, name: str, status: str) -> None:
    run = results / name
    run.mkdir(parents=True)
    (run / "meta.json").write_text(json.dumps({"status": status}), encoding="utf-8")
    (run / "stream.jsonl").write_text("{}\n", encoding="utf-8")


def test_pack_zips_finished_runs_at_repo_relative_paths(tmp_path: Path):
    results = tmp_path / "results"
    _run(results, "martian/board-full/grafana-1/r1", "ok")
    _run(results, "martian/code-review/grafana-1/r1", "failed:usage-limit")
    out = tmp_path / "packs" / "pilot.zip"
    runs = [("martian", v, "grafana-1", 1) for v in ("board-full", "code-review")] + [("martian", "board-full", "x-2", 1)]

    packed, skipped = pack_runs(results, runs, out)

    assert packed == ["martian/board-full/grafana-1/r1"]
    assert skipped == ["martian/code-review/grafana-1/r1 (failed:usage-limit)", "martian/board-full/x-2/r1 (missing)"]
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read("eval/results/manifest.json"))
    assert {"eval/results/martian/board-full/grafana-1/r1/meta.json",
            "eval/results/martian/board-full/grafana-1/r1/stream.jsonl"} <= names
    assert not any("code-review" in n for n in names)
    assert manifest["runs"] == packed and len(manifest["skipped"]) == 2
