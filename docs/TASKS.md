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

- `<variant>`: `code-review`, `board-full`, or `board-<id>` for a single-member run (Y7). The default board (b) is never run; scoring computes it from `board-full`.
- `<pr_id>` and `<dataset>` come from `eval/datasets/<dataset>/prs.jsonl` (Y1). `<pr_id>` must be usable as a folder name.
- `split.json` (Y3): `{"seed": <int>, "dev": [pr_id, ...], "test": [pr_id, ...]}`.
- Scoring writes its own records to `eval/results/<dataset>/judged/` (git-ignored). `eval/datasets/smoke/` is a one-PR example of the dataset format.

## Account A side (Jim)

| id | Task | Status |
|---|---|---|
| J1 | **The skill** (PLAN Phase 1): plugin, members, verifier, smoke test | ✅ first version, smoke-tested (see `eval/smoke/README.md`) |
| J2 | **Scoring:** run Martian's extract, dedup, and judge steps unchanged; subset scoring (PLAN 6.2); metrics and the paired bootstrap. See `eval/scoring/README.md` | ✅ built and tested offline; reproduces Martian's published numbers. The live smoke check stopped at the endpoint quota, with the board's per-finding record still to judge |
| J3 | **Judge gates:** Gate 1 with one paid published judge; Gate 1b for each local judge, including a repeat to test temperature-0 repeatability; a drift check at each judging session (re-judge a fixed sample of pairs), because the endpoint reports no model version. Pace calls for the shared endpoint (PLAN 5.1) | `gate1` (Sonnet 4.5 on OpenRouter) and `gate1b` with the repeat check built; Gate 1 **passed** 2026-11-03 (96.5% agreement, F1 40.1% vs 38.7%); Gate 1b for `deepseek-flash` **passed** 2026-11-04 (mean tau +0.97 vs floor +0.90; repeat check 200/200 same); Gate 1b for `glm-flash` **passed** 2026-11-05 (mean tau +0.97; repeat check 199/200 same) |
| J4 | **Skill support for Phase 4b:** `model:` in member briefs, and `--verifier-model` | ☐, only if Y6 goes ahead |
| J5 | **Member card and combined scoring** (PLAN 6.5, 6.7): the card in `python -m eval.scoring members`; scoring a single-member run (Y7) combined with the other members' findings from the full run. See "Testing one member" below | card ✅ (2026-11-04); combined scoring ☐, needs Y7 |

Judging runs only on account A's machine, because the local endpoint key is personal.

## Account B side (Yara)

| id | Task | Done when | Status |
|---|---|---|---|
| Y1 | **Fetch:** clone each Martian PR's fork with `core.autocrlf=false`, record the base and head SHAs, and decide which PRs are runnable | `eval/datasets/martian/prs.jsonl` has every PR with `id, repo, base, head, intent`, plus `runnable` and a reason when false. The intent is the PR title and description. Report the runnable count (PLAN assumes ~40) | ✅ **45 of 50 runnable**; the 5 unrunnable are exactly Martian's flagged PRs. `uv run python -m eval.runner fetch` builds the per-PR repositories (19 GB) from the SHAs in `prs.jsonl` |
| Y2 | **Dataset adapter:** convert the pinned golden comments | `eval/datasets/martian/golden.jsonl` with `pr_id, comment, severity, category`. Every golden comment maps to exactly one PR in `prs.jsonl`. The 5 PRs with an `az_comment` are marked unrunnable | ✅ 173 golden comments, 154 on runnable PRs; `eval.scoring.data.load_dataset("martian")` reads both files |
| Y3 | **Freeze the split** (PLAN Phase 3 step 1), before any result exists | `eval/datasets/martian/split.json`: about 15 development and 25 test PRs from the runnable ones, stratified by repo, with the random seed recorded. Never changed afterwards | ✅ all 45 runnable PRs: 15 development, 30 test (3 development per repo), seed 20261103, plus the 10 pilot PRs. `python -m eval.runner split` refuses to overwrite it |
| Y4 | **Runner** (PLAN Phase 2, steps 2–4 and 7) | For one (variant, PR, repeat): a fresh worktree at the head commit, the pinned `claude -p` command with `--model`, `--effort`, `--plugin-dir`, the allowed and disallowed tools, and the clean `CLAUDE_CONFIG_DIR`. It checks the `init` event, detects usage-limit stops and resumes later, and writes the layout above. Test it on the smoke repo (`eval/smoke/`), not on benchmark PRs. Parse `final.json` tolerantly: strip a code fence, and restore a missing opening `{`. The campus model dropped it once in the smoke test. Record a repaired parse in `meta.json`. Intent (PLAN 5.1): write the PR title and description to a file outside the worktree and pass `--intent-file` to the board. For `/code-review`, first test on the smoke repo that the same text after the target leaves its review range unchanged; if it doesn't, give the text to neither variant and tell Jim. For `/code-review`, a rejected first `ReportFindings` call (for example `short_summary` over 60 characters) is normal: the model retries it, so it isn't a failed run | ✅ `uv run python -m eval.runner run` (usage in `eval/runner/__main__.py`); smoke-tested on account B |
| Y5 | **Review the skill pull request** (J1) as a second reader | review comments on the pull request | ✅ review of #8, addressed in #10 |
| Y7 | **Single-member runs** (PLAN 6.7): run one new or edited member, with its verifiers, on the development PRs, using only that member's usage. See "Testing one member" below | `run --variant board-full --members <id> --set dev --repeat N` writes `board-<id>` runs and the member's cache file; `pack` packs them; tests cover the refusals | ✅ 2026-11-04. Two additions: `run` stops before starting on finished board runs made with an older plugin, and refuses a member whose brief is unchanged since the full run of the same PR and repeat, because the cache path doesn't name the variant and the single run would overwrite the full run's cache file |
| Y6 | *Optional:* **Phase 4b**, stronger models for selected roles (PLAN Phase 4b) | the decision and its numbers, in `eval/reports/` | after Phase 4 |
| Y8 | **Third pilot, the 12-member board** (PLAN Phase 3, Gate 2 v2). See "Third pilot" below | board r1 and r2 and `/code-review` r2 on the 10 pilot PRs, all `ok`, packed and sent | ✅ 2026-11-04, 40 runs. Gate 2 v2: FAIL |
| Y9 | **Gate 2 on Qodo** (PLAN Phase 3). See "Qodo (Y9)" below | `prs.jsonl` and `split.json` for Qodo merged; board and `/code-review`, 2 repeats each, on the 24 sampled PRs, all `ok`, packed and sent | ☐ |
| — | Runs on account B (Phases 3–5), as assigned in PLAN 7.3 | | later |

Y1–Y4 cost no Claude usage, except a few smoke-repo runs to test Y4.

## Third pilot (Y8)

The design changed after the second pilot failed Gate 2 (PLAN Phase 3): four new members (`line-reader`, `regression`, `boundary`, `test-auditor`), so the full roster has 12; a tighter grouping rule in `SKILL.md`; and "one problem per finding" in `board-member.md`. The new Gate 2 rule compares **recall**, with **2 repeats per PR for both variants**, so `/code-review` needs a second repeat too.

1. Pull `main` and check the commit you run is the one that adds the four members (`git log -1`).
2. **Move the old board runs aside**, because the runner skips any finished run whatever its plugin hash: move `eval/results/martian/board-full/` to `eval/results/superseded/board-full-pilot-893973e/`. Keep `eval/results/martian/code-review/` where it is: its repeat 1 counts.
3. Check with `--dry-run` first: every board run should say `to run`, and `/code-review` repeat 1 `done`. Then run, in this order, so a usage stop leaves complete pairs first:
   - `uv run python -m eval.runner run --variant board-full --set pilot --repeat 1`
   - `uv run python -m eval.runner run --variant code-review --set pilot --repeat 2`
   - `uv run python -m eval.runner run --variant board-full --set pilot --repeat 2`
4. Check: every run `ok`, `permission_denials` 0, and `members.ran` in `final.json` lists the members whose triggers matched. A member that failed shows up in `members.failed`: report it, don't re-run silently.
5. Pack both repeats, `uv run python -m eval.runner pack --variant both --set pilot --repeat 1` and the same with `--repeat 2`, and send both zips. Note anything unusual in your session notes, as before: denials, skipped members, a much longer run.

**Budget.** The last pilot cost $7.30 for 10 board runs with 8 members. With 12 members expect about $10–12 per repeat, so about $25 for both repeats; `/code-review` adds under $1.

## Qodo (Y9)

Gate 2 failed on Martian. The same rule is now tested on Qodo PR-Review-Bench, a dataset no brief was written from (PLAN Phase 3, "Gate 2 on Qodo"; `eval/datasets/qodo/SOURCE.md`). The plugin is unchanged since Y8, and no member is tuned.

1. Pull `main`.
2. **Fetch:** `uv run python -m eval.runner fetch --dataset qodo`. It clones the 8 forks under `agentic-review-benchmarks` into the data directory, about 3 GB (cal.com alone is about 1 GB), and builds one repository per PR. Report the runnable count; all 100 PRs were open with fetchable heads on 2026-11-05.
3. **Freeze the sample**, before any run: `uv run python -m eval.runner split --dataset qodo --dev-per-repo 3 --pilot-per-repo 3 --seed 20261105`. That gives 24 PRs, 3 per repository. Open a pull request with `eval/datasets/qodo/prs.jsonl` and `split.json` only, and wait for it to be merged before running.
4. **Run**, with `--dataset-dir eval/datasets/qodo` on every command (results go to `eval/results/qodo/`). Check with `--dry-run` first, then in this order, so a usage stop leaves complete pairs first:
   - `uv run python -m eval.runner run --dataset-dir eval/datasets/qodo --variant board-full --set pilot --repeat 1`
   - `uv run python -m eval.runner run --dataset-dir eval/datasets/qodo --variant code-review --set pilot --repeat 1`
   - the same two with `--repeat 2`
5. **Check** as in Y8: every run `ok`, `permission_denials` 0, `members.failed` empty. Report anything unusual rather than re-running silently.
6. **Pack** each repeat: `uv run python -m eval.runner pack --dataset-dir eval/datasets/qodo --variant both --set pilot --repeat 1`, and the same with `--repeat 2`. Send both zips.

**Don't open the PRs' GitHub pages or their comments.** Five Ghost PRs carry an outside comment that lists the injected issues (SOURCE.md). The reviewers can't reach it, and neither should anyone who might later look at the findings.

**Budget.** Y8 cost $19.12 for 20 board runs, about $1 each. Qodo PRs are often larger (median 208 changed lines), so expect $1–1.5 per board run: about $50–70 for 48 board runs, plus about $4 for 48 `/code-review` runs. That's within the usage ceiling (PLAN section 7); if a usage stop comes first, run the same command again after the reset.

## Testing one member (Y7 and J5)

**Why.** The pilot showed that one member can hold the board back: the Saboteur found 0 of the 7 security issues in its own category (`eval/reports/pilot-gate2-deepseek-flash.md`). The member card (`python -m eval.scoring members`) shows which member it is. Fixing a member means editing its brief and measuring it again. Re-running the whole board for that costs about $7 per pilot; running only the edited member costs its own share plus its verifiers. In the pilot, each member used 7–13% of the board's subagent usage, and the verifiers 20%.

### Y7, the runner (Yara)

1. **Allow `--members` on benchmark PRs.**
   - Today `run` refuses `--members` without `--smoke-repo` (`eval/runner/__main__.py`, `_run_specs`).
   - Allow exactly one member id, with `--set dev`, `--set pilot`, or `--prs` naming development PRs only.
   - Refuse it with `--set test` or `--set all`, or with a test PR in `--prs`. Members are tuned on development PRs only (PLAN 6.6), so test runs would spend usage on numbers nobody may look at yet.
   - Refuse an unknown id. The valid ids are the files in `plugin/skills/review/members/`.
   - Several ids at once stay smoke-only.
2. **The command** is the board with that roster: `/board-review:review --members <id> --max-findings 8 --format json ...`. The orchestrator runs that one member, then verifies only its findings, with the same pass 1 and pass 2 rules.
   - The member's trigger still applies. If the trigger doesn't match a PR, the run ends with the member under `members.skipped`.
   - Such a run is `ok`, and its cache file has `status: skipped` with the reason. Scoring needs to tell a skipped member from a missing run.
3. **Where results go.**
   - The variant folder is `board-<id>`, as the smoke convention already does: `eval/results/<dataset>/board-<id>/<pr_id>/r<repeat>/`, with the same files as `board-full`.
   - Use the **same repeat numbers** as the full runs it will be combined with (`r1`, `r2`). Scoring pairs a single-member run with the full run of the same PR and repeat.
   - `_write_cache` already writes the member's cache file. Its path differs from the full run's only in `<member-file-hash>`, because `<config-hash>` excludes member files. Keep it that way: an edited brief must give a new cache file next to the old one, never overwrite it.
4. **`meta.json`** records the roster (`roster: <id>`) and, as now, `skill_hashes` with the member's file hash. Scoring checks that the hash matches the brief it is told to score.
5. **Stale runs.**
   - A `board-<id>` run is outdated when that member's file hash, or the `config_hash`, differs from the current plugin.
   - Your proposed stop-on-stale check should cover this case: stop and list the stale runs, rather than skip them as done.
   - Moving the old folder aside by hand stays the way to re-run.
6. **`pack`** must accept `--variant board-full --members <id>` and pack the `board-<id>` runs.
   - Also pack the cache files of the packed runs: `eval/cache/<dataset>/<pr_id>/r<repeat>/<id>@<model>/...`. Zips don't hold cache files today.
   - Scoring reads the member's findings from `final.json`; the cache file is the check that the hashes agree.
7. **Tests:**
   - the refusals: a test PR, several ids on benchmark PRs, an unknown id;
   - the `board-<id>` folder name;
   - a skipped member gives an `ok` run with a `skipped` cache entry;
   - `pack` includes the cache file.
8. **Budget.** The pilot is 10 PRs × 1 repeat; the development split is 15 PRs × 2 repeats. Run the pilot first, as a mechanics check.

### J5, scoring (Jim)

Combine a `board-<id>` run with the full run of the same PR and repeat: take the full run's findings, drop that member's, and add the single-member run's findings, with their labels.

- **Judging.** Only the new candidates need judge calls; the rest come from the full run's judged record.
- **Grouping.** The board's `group` ids don't carry across runs, so a combined record groups duplicates with Martian's dedup over the union, and counts once per group as now. To compare like with like, the card compares it with the full board regrouped the same way.
- **A known difference.** In a full run, a duplicate that two members found is verified once, as one group. In a combined record, each side keeps the label from its own run. Only duplicates are affected, and the card reports how many there are.
- **Output.** The edited member's card, with the same columns as now, against the unedited full board. Promotion follows PLAN Phase 4's rule.

## Order

1. Now, in parallel: Y1, Y2, Y4. (J1, J2, and Y5 are done.)
2. Y3 as soon as Y1 gives the runnable count.
3. J3 needs Y2 (golden comments in our format) and J2.
4. Phase 3 starts when J1, J2, J3, Y3, and Y4 are done.
