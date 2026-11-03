# Tasks

Who builds what, and the interfaces between the two sides, so both can work in parallel. Pick a task, open a branch named after its id (for example `y1-fetch`), and tick it here in your pull request.

- **Account A side:** Jim, with Claude Code.
- **Account B side:** Yara.

## The interface between the sides

The runner (Y4) writes and the scoring (J2) reads this layout. Agree on changes in a pull request before either side builds against a different layout.

```
eval/results/<dataset>/<variant>/<pr_id>/r<repeat>/     (git-ignored)
  stream.jsonl     raw `claude -p --output-format stream-json --verbose` output, kept whole: scoring reads
                   variant a's findings from its last accepted ReportFindings tool call in it
  final.json       variant c: the board's `board_output` JSON (plugin/skills/review/schema.json)
  final.md         variant a: the `/code-review` text reply (scored only if no ReportFindings call was accepted)
  meta.json        run metadata from PLAN 6.4, plus status: ok | failed:<reason> | failed:usage-limit
  stderr.txt       the CLI's stderr
eval/cache/<dataset>/<pr_id>/r<repeat>/<member>@<model>/<config-hash>/<member-file-hash>.json   (git-ignored)
  one member's findings from final.json, split out by the runner (PLAN 6.2)
```

- `<config-hash>`: the rest of the PLAN 6.2 cache key, which changes a member's labeled findings: effort, Claude Code version, and the hashes of `SKILL.md`, `schema.json`, `roster.json`, and both agent files. `meta.json` lists the inputs as `skill_hashes` and `config_hash`.
- A cache file holds `member`, `status` (`ran`, or `skipped` with the trigger `reason`), its `findings` as in `final.json` (with `group` and `label`), the intern's `explanations`, and `source_run`. A failed member gets no file.

- `<variant>`: `code-review` or `board-full`. The default board (b) is never run; scoring computes it from `board-full`.
- `<pr_id>` and `<dataset>` come from `eval/datasets/<dataset>/prs.jsonl` (Y1). `<pr_id>` must be usable as a folder name.
- `split.json` (Y3): `{"seed": <int>, "dev": [pr_id, ...], "test": [pr_id, ...]}`.
- Scoring writes its own records to `eval/results/<dataset>/judged/` (git-ignored). `eval/datasets/smoke/` is a one-PR example of the dataset format.

## Account A side (Jim)

| id | Task | Status |
|---|---|---|
| J1 | **The skill** (PLAN Phase 1): plugin, members, verifier, smoke test | ✅ first version, smoke-tested (see `eval/smoke/README.md`) |
| J2 | **Scoring:** run Martian's extract, dedup, and judge steps unchanged; subset scoring (PLAN 6.2); metrics and the paired bootstrap. See `eval/scoring/README.md` | ✅ built and tested offline; reproduces Martian's published numbers. The live smoke check stopped at the endpoint quota, with the board's per-finding record still to judge |
| J3 | **Judge gates:** Gate 1 with one paid published judge; Gate 1b for each local judge, including a repeat to test temperature-0 repeatability; a drift check at each judging session (re-judge a fixed sample of pairs), because the endpoint reports no model version. Pace calls for the shared endpoint (PLAN 5.1) | `gate1` (Sonnet 4.5 on OpenRouter) and `gate1b` with the repeat check built; Gate 1 run 2026-11-03, Gate 1b for `deepseek-flash` overnight |
| J4 | **Skill support for Phase 4b:** `model:` in member briefs, and `--verifier-model` | ☐, only if Y6 goes ahead |
| — | Runs on account A (Phases 3–5), as assigned in PLAN 7.3 | later |

Judging runs only on account A's machine, because the local endpoint key is personal.

## Account B side (Yara)

| id | Task | Done when | Status |
|---|---|---|---|
| Y1 | **Fetch:** clone each Martian PR's fork with `core.autocrlf=false`, record the base and head SHAs, and decide which PRs are runnable | `eval/datasets/martian/prs.jsonl` has every PR with `id, repo, base, head, intent`, plus `runnable` and a reason when false. The intent is the PR title and description. Report the runnable count (PLAN assumes ~40) | ✅ **45 of 50 runnable**; the 5 unrunnable are exactly Martian's flagged PRs. `uv run python -m eval.runner fetch` builds the per-PR repositories (19 GB) from the SHAs in `prs.jsonl` |
| Y2 | **Dataset adapter:** convert the pinned golden comments | `eval/datasets/martian/golden.jsonl` with `pr_id, comment, severity, category`. Every golden comment maps to exactly one PR in `prs.jsonl`. The 5 PRs with an `az_comment` are marked unrunnable | ✅ 173 golden comments, 154 on runnable PRs; `eval.scoring.data.load_dataset("martian")` reads both files |
| Y3 | **Freeze the split** (PLAN Phase 3 step 1), before any result exists | `eval/datasets/martian/split.json`: about 15 development and 25 test PRs from the runnable ones, stratified by repo, with the random seed recorded. Never changed afterwards | ✅ all 45 runnable PRs: 15 development, 30 test (3 development per repo), seed 20261103, plus the 10 pilot PRs. `python -m eval.runner split` refuses to overwrite it |
| Y4 | **Runner** (PLAN Phase 2, steps 2–4 and 7) | For one (variant, PR, repeat): a fresh worktree at the head commit, the pinned `claude -p` command with `--model`, `--effort`, `--plugin-dir`, the allowed and disallowed tools, and the clean `CLAUDE_CONFIG_DIR`. It checks the `init` event, detects usage-limit stops and resumes later, and writes the layout above. Test it on the smoke repo (`eval/smoke/`), not on benchmark PRs. Parse `final.json` tolerantly: strip a code fence, and restore a missing opening `{`. The campus model dropped it once in the smoke test. Record a repaired parse in `meta.json`. Intent (PLAN 5.1): write the PR title and description to a file outside the worktree and pass `--intent-file` to the board. For `/code-review`, first test on the smoke repo that the same text after the target leaves its review range unchanged; if it doesn't, give the text to neither variant and tell Jim. For `/code-review`, a rejected first `ReportFindings` call (for example `short_summary` over 60 characters) is normal: the model retries it, so it isn't a failed run | ✅ `uv run python -m eval.runner run` (usage in `eval/runner/__main__.py`); smoke-tested on account B |
| Y5 | **Review the skill pull request** (J1) as a second reader | review comments on the pull request | ✅ review of #8, addressed in #10 |
| Y6 | *Optional:* **Phase 4b**, stronger models for selected roles (PLAN Phase 4b) | the decision and its numbers, in `eval/reports/` | after Phase 4 |
| — | Runs on account B (Phases 3–5), as assigned in PLAN 7.3 | | later |

Y1–Y4 cost no Claude usage, except a few smoke-repo runs to test Y4.

## Order

1. Now, in parallel: Y1, Y2, Y4. (J1, J2, and Y5 are done.)
2. Y3 as soon as Y1 gives the runnable count.
3. J3 needs Y2 (golden comments in our format) and J2.
4. Phase 3 starts when J1, J2, J3, Y3, and Y4 are done.
