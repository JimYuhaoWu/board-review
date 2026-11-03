"""Data pipeline and runner: the Martian dataset, the split, and benchmark runs (TASKS Y1-Y4).

Usage (from the repo root, after `uv sync`):
    uv run python -m eval.runner fetch  [--data-dir DIR] [--refresh]   # Y1: prs.jsonl, one repo per PR
    uv run python -m eval.runner golden                                # Y2: golden.jsonl
    uv run python -m eval.runner split  [--dev-per-repo N]             # Y3: split.json, written once
    uv run python -m eval.runner run --variant V (--prs IDS | --set S) --repeat N [--dry-run]   # Y4
    uv run python -m eval.runner run --variant V --smoke-repo DIR --base SHA --head SHA \\
        --repeat N [--members IDS] [--intent-file FILE]                                       # mechanics test
    uv run python -m eval.runner pack --variant V (--prs IDS | --set S) --repeat N            # zip for judging

`run` exits 0 when every run is done, 1 when some failed, and 3 when it stopped at a plan usage
limit. Run the same command again after the reset; finished runs are skipped.

Repositories go to --data-dir, outside this repository, so a reviewer working in them can't read
the golden comments. The default is $BOARD_EVAL_DATA, or ~/board-review-data.

`fetch` reuses the SHAs and intent already in prs.jsonl, so the other machine builds the same
repositories without asking GitHub again. --refresh looks every PR up again.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import os
from pathlib import Path

from eval.runner import martian
from eval.runner.pack import pack_runs
from eval.runner.records import DATASET_DIR, ROOT, PullRequest, read_jsonl, write_jsonl
from eval.runner.run import VARIANTS, Paths, RunSpec, check_claude, command_for, is_done, run_one
from eval.runner.split import Split, make_split, write_frozen


def _default_data_dir() -> Path:
    return Path(os.environ.get("BOARD_EVAL_DATA", Path.home() / "board-review-data"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.runner", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="look up the Martian PRs and build one repository per PR")
    fetch.add_argument("--data-dir", type=Path, default=_default_data_dir())
    fetch.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    fetch.add_argument("--refresh", action="store_true", help="look every PR up on GitHub again")

    golden = sub.add_parser("golden", help="convert the Martian golden comments")
    golden.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)

    split = sub.add_parser("split", help="freeze the development / test split")
    split.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    split.add_argument("--dev-per-repo", type=int, default=3)
    split.add_argument("--pilot-per-repo", type=int, default=2)

    run = sub.add_parser("run", help="run variants through claude -p (Y4)")
    _add_selection(run)
    run.add_argument("--cache-dir", type=Path, default=ROOT / "eval" / "cache")
    run.add_argument("--config-dir", type=Path, default=Path.home() / ".claude-bench")
    run.add_argument("--claude", default="claude", help="the pinned standalone Claude Code")
    run.add_argument("--dry-run", action="store_true", help="print the commands; run nothing")

    pack = sub.add_parser("pack", help="zip finished runs for the judging machine")
    _add_selection(pack)
    pack.add_argument("--out", type=Path, help="default: eval/results/packs/<dataset>-<which>-r<N>-<time>.zip")

    args = parser.parse_args(argv)
    if args.command == "fetch":
        return _fetch(args)
    if args.command == "golden":
        return _golden(args)
    if args.command == "split":
        return _split(args)
    if args.command == "pack":
        return _pack(args)
    return _run(args)


def _add_selection(p: argparse.ArgumentParser) -> None:
    """Which runs: the variant, the PRs, and the repeat. Shared by `run` and `pack`."""
    p.add_argument("--variant", choices=[*VARIANTS, "both"], required=True)
    p.add_argument("--repeat", type=int, required=True)
    which = p.add_mutually_exclusive_group(required=True)
    which.add_argument("--prs", help="comma-separated PR ids")
    which.add_argument("--set", choices=["pilot", "dev", "test", "all"], help="PRs from split.json")
    which.add_argument("--smoke-repo", type=Path, help="a repository from eval/smoke/make_repo.sh")
    p.add_argument("--base", help="base SHA, with --smoke-repo")
    p.add_argument("--head", help="head SHA, with --smoke-repo")
    p.add_argument("--members", default="full", help="board roster; anything but full only with --smoke-repo")
    p.add_argument("--intent-file", type=Path, help="PR title and description, with --smoke-repo")
    p.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    p.add_argument("--data-dir", type=Path, default=_default_data_dir())
    p.add_argument("--results-dir", type=Path, default=ROOT / "eval" / "results")


def _fetch(args: argparse.Namespace) -> int:
    prs_path = args.dataset_dir / "prs.jsonl"
    known = {}
    if prs_path.exists() and not args.refresh:
        known = {pr.id: pr for pr in read_jsonl(prs_path, PullRequest)}
    prs = martian.build_pull_requests(martian.load_entries(), args.data_dir, known)
    write_jsonl(prs_path, prs)

    runnable = collections.Counter(pr.repo for pr in prs if pr.runnable)
    total = collections.Counter(pr.repo for pr in prs)
    for repo in total:
        print(f"{repo}: {runnable[repo]} of {total[repo]} runnable")
    for pr in prs:
        if not pr.runnable:
            print(f"  not runnable: {pr.id}: {pr.reason}")
        elif pr.agent_files:
            print(f"  agent files: {pr.id}: {', '.join(pr.agent_files)}")
    print(f"total: {sum(runnable.values())} of {len(prs)} runnable -> {prs_path}")
    return 0


def _golden(args: argparse.Namespace) -> int:
    records = martian.golden_records(martian.load_entries())
    prs_path = args.dataset_dir / "prs.jsonl"
    if prs_path.exists():
        ids = {pr.id for pr in read_jsonl(prs_path, PullRequest)}
        missing = sorted({r.pr_id for r in records} - ids)
        if missing:
            print(f"golden comments for PRs missing from prs.jsonl: {missing}")
            return 1
    path = args.dataset_dir / "golden.jsonl"
    write_jsonl(path, records)
    print(f"{len(records)} golden comments -> {path}")
    return 0


def _split(args: argparse.Namespace) -> int:
    prs = read_jsonl(args.dataset_dir / "prs.jsonl", PullRequest)
    result = make_split(prs, dev_per_repo=args.dev_per_repo, pilot_per_repo=args.pilot_per_repo)
    path = args.dataset_dir / "split.json"
    write_frozen(path, result)
    print(f"seed {result.seed}: {len(result.dev)} development, {len(result.test)} test -> {path}")
    print(f"pilot: {', '.join(result.pilot)}")
    return 0


def _run_specs(args: argparse.Namespace) -> list[RunSpec]:
    variants = VARIANTS if args.variant == "both" else (args.variant,)
    if args.members != "full":
        if not args.smoke_repo:
            raise SystemExit("--members is for smoke tests; benchmark runs use the full board")
        variants = tuple(f"board-{args.members.replace(',', '+')}" if v == "board-full" else v for v in variants)
    if args.smoke_repo:
        if not (args.base and args.head):
            raise SystemExit("--smoke-repo needs --base and --head")
        intent = args.intent_file.read_text(encoding="utf-8").strip() if args.intent_file else ""
        targets = [("smoke", "smoke", args.smoke_repo.resolve(), args.base, args.head, intent)]
    else:
        prs = {pr.id: pr for pr in read_jsonl(args.dataset_dir / "prs.jsonl", PullRequest)}
        if args.prs:
            ids = args.prs.split(",")
        else:
            split = Split.model_validate_json((args.dataset_dir / "split.json").read_text(encoding="utf-8"))
            ids = split.dev + split.test if args.set == "all" else getattr(split, args.set)
        for pr_id in ids:
            if pr_id not in prs or not prs[pr_id].runnable:
                raise SystemExit(f"{pr_id} is not a runnable PR in prs.jsonl")
        dataset = args.dataset_dir.name
        targets = [
            (dataset, i, args.data_dir.resolve() / "prs" / f"{i}.git", prs[i].base, prs[i].head, prs[i].intent)
            for i in ids
        ]
    return [
        RunSpec(
            dataset=d, pr_id=i, variant=v, repeat=args.repeat, repo=repo, base=base, head=head,
            members=args.members, intent=intent,
        )
        for d, i, repo, base, head, intent in targets
        for v in variants
    ]


def _pack(args: argparse.Namespace) -> int:
    specs = _run_specs(args)
    which = args.set or ("smoke" if args.smoke_repo else f"{len(specs)}runs")
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    out = args.out or args.results_dir / "packs" / f"{specs[0].dataset}-{which}-r{args.repeat}-{stamp}.zip"
    packed, skipped = pack_runs(
        args.results_dir, [(s.dataset, s.variant, s.pr_id, s.repeat) for s in specs], out
    )
    for name in skipped:
        print(f"  not packed: {name}")
    print(f"{len(packed)} runs -> {out} ({out.stat().st_size / 1e6:.1f} MB)")
    print("On the judging machine, extract it at the repo root: uv run python -m zipfile -e <zip> .")
    return 0 if packed else 1


def _run(args: argparse.Namespace) -> int:
    paths = Paths(
        results=args.results_dir, cache=args.cache_dir, work=args.data_dir.resolve() / "work",
        config_dir=args.config_dir, claude=args.claude,
    )
    specs = _run_specs(args)
    if args.dry_run:
        for spec in specs:
            state = "done" if is_done(paths, spec) else "to run"
            print(f"{spec.dataset}/{spec.variant}/{spec.pr_id}/r{spec.repeat} ({state}) in {spec.repo}")
            intent_file = Path("<intent-file>") if spec.intent and spec.variant.startswith("board-") else None
            print("  " + " ".join(command_for(spec, paths.claude, intent_file)[1:]))
        return 0

    account = check_claude(paths)
    failed = 0
    for spec in specs:
        name = f"{spec.dataset}/{spec.variant}/{spec.pr_id}/r{spec.repeat}"
        if is_done(paths, spec):
            print(f"{name}: done, skipped")
            continue
        meta = run_one(spec, paths, account)
        print(f"{name}: {meta.status} ({meta.wall_seconds}s){': ' + meta.detail if meta.detail else ''}")
        if meta.status == "failed:usage-limit":
            print("stopped at the plan usage limit; run the same command again after the reset")
            return 3
        failed += meta.status != "ok"
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
