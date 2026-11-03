"""Zip a batch of finished runs for the judging machine (PLAN 5.1: judging runs on account A only).

The zip holds `eval/results/<dataset>/<variant>/<pr_id>/r<repeat>/` for every run whose status
is ok, with paths relative to the repo root, plus a `manifest.json`. Extract it at the repo root
on the other machine, for example `uv run python -m zipfile -e <zip> .`.
"""

from __future__ import annotations

import datetime as dt
import json
import zipfile
from pathlib import Path

from eval.runner import gitrepo
from eval.runner.records import ROOT


def pack_runs(results: Path, runs: list[tuple[str, str, str, int]], out: Path) -> tuple[list[str], list[str]]:
    """Zip the finished ones of (dataset, variant, pr_id, repeat). Returns (packed, skipped)."""
    packed, skipped = [], []
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for dataset, variant, pr_id, repeat in runs:
            name = f"{dataset}/{variant}/{pr_id}/r{repeat}"
            run = results / name
            meta = run / "meta.json"
            status = json.loads(meta.read_text(encoding="utf-8")).get("status") if meta.exists() else "missing"
            if status != "ok":
                skipped.append(f"{name} ({status})")
                continue
            for path in sorted(p for p in run.rglob("*") if p.is_file()):
                zf.write(path, (Path("eval/results") / path.relative_to(results)).as_posix())
            packed.append(name)
        manifest = {
            "created": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "commit": gitrepo.git("-C", str(ROOT), "describe", "--always", "--dirty", "--abbrev=12").strip(),
            "runs": packed,
            "skipped": skipped,
        }
        zf.writestr("eval/results/manifest.json", json.dumps(manifest, indent=2) + "\n")
    return packed, skipped
