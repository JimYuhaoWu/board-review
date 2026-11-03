# Tasks

Who builds what, and the interfaces between the two sides, so both can work in parallel. Pick a task, open a branch named after its id (for example `y1-fetch`), and tick it here in your pull request.

- **Account A side:** Jim, with Claude Code.
- **Account B side:** Yara.

## The interface between the sides

The runner (Y4) writes and the scoring (J2) reads this layout. Agree on changes in a pull request before either side builds against a different layout.

```
eval/results/<dataset>/<variant>/<pr_id>/r<repeat>/     (git-ignored)
  stream.jsonl     raw `claude -p --output-format stream-json --verbose` output
  final.json       variant c: the board's `board_output` JSON (plugin/skills/review/schema.json)
  final.md         variant a: the `/code-review` text reply
  meta.json        run metadata from PLAN 6.4, plus status: ok | failed:<reason> | failed:usage-limit
eval/cache/<dataset>/<pr_id>/r<repeat>/<member>@<model>/<member-file-hash>.json   (git-ignored)
  one member's findings from final.json, split out by the runner (PLAN 6.2)
```

- `<variant>`: `code-review` or `board-full`. The default board (b) is never run; scoring computes it from `board-full`.
- `<pr_id>` and `<dataset>` come from `eval/datasets/<dataset>/prs.jsonl` (Y1).

## Account A side (Jim)

| id | Task | Status |
|---|---|---|
| J1 | **The skill** (PLAN Phase 1): plugin, members, verifier, smoke test | ✅ first version, smoke-tested (see `eval/smoke/README.md`) |
| J2 | **Scoring:** render `final.json` and `final.md` as one Martian review comment; run Martian's extract, dedup, and judge steps unchanged; subset scoring (PLAN 6.2); metrics and the paired bootstrap | ☐ |
| J3 | **Judge gates:** Gate 1 with one paid published judge; Gate 1b for each local judge, including a repeat to test temperature-0 repeatability; log the served model version | ☐ |
| J4 | **Skill support for Phase 4b:** `model:` in member briefs, and `--verifier-model` | ☐, only if Y6 goes ahead |
| — | Runs on account A (Phases 3–5), as assigned in PLAN 7.3 | later |

Judging runs only on account A's machine, because the local endpoint key is personal.

## Account B side (Yara)

| id | Task | Done when | Status |
|---|---|---|---|
| Y1 | **Fetch:** clone each Martian PR's fork with `core.autocrlf=false`, record the base and head SHAs, and decide which PRs are runnable | `eval/datasets/martian/prs.jsonl` has every PR with `id, repo, base, head, intent`, plus `runnable` and a reason when false. The intent is the PR title and description. Report the runnable count (PLAN assumes ~40) | ☐ |
| Y2 | **Dataset adapter:** convert the pinned golden comments | `eval/datasets/martian/golden.jsonl` with `pr_id, comment, severity, category`. Every golden comment maps to exactly one PR in `prs.jsonl`. The 5 PRs with an `az_comment` are marked unrunnable | ☐ |
| Y3 | **Freeze the split** (PLAN Phase 3 step 1), before any result exists | `eval/datasets/martian/split.json`: about 15 development and 25 test PRs from the runnable ones, stratified by repo, with the random seed recorded. Never changed afterwards | ☐ |
| Y4 | **Runner** (PLAN Phase 2, steps 2–4 and 7) | For one (variant, PR, repeat): a fresh worktree at the head commit, the pinned `claude -p` command with `--model`, `--effort`, `--plugin-dir`, the allowed and disallowed tools, and the clean `CLAUDE_CONFIG_DIR`. It checks the `init` event, detects usage-limit stops and resumes later, and writes the layout above. Test it on the smoke repo (`eval/smoke/`), not on benchmark PRs. Parse `final.json` tolerantly: strip a code fence, and restore a missing opening `{`. The campus model dropped it once in the smoke test. Record a repaired parse in `meta.json`. Intent (PLAN 5.1): write the PR title and description to a file outside the worktree and pass `--intent-file` to the board. For `/code-review`, first test on the smoke repo that the same text after the target leaves its review range unchanged; if it doesn't, give the text to neither variant and tell Jim | ☐ |
| Y5 | **Review the skill pull request** (J1) as a second reader | review comments on the pull request | ☐ |
| Y6 | *Optional:* **Phase 4b**, stronger models for selected roles (PLAN Phase 4b) | the decision and its numbers, in `eval/reports/` | after Phase 4 |
| — | Runs on account B (Phases 3–5), as assigned in PLAN 7.3 | | later |

Y1–Y4 cost no Claude usage, except a few smoke-repo runs to test Y4.

## Order

1. Now, in parallel: J1, J2, Y1, Y2, Y4, Y5.
2. Y3 as soon as Y1 gives the runnable count.
3. J3 needs Y2 (golden comments in our format) and J2.
4. Phase 3 starts when J1, J2, J3, Y3, and Y4 are done.
