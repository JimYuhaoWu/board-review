# Scoring (task J2)

Scores finished runs (written by the runner, Y4, in the layout of [docs/TASKS.md](../../docs/TASKS.md)) with Martian's extraction, dedup, and judge steps, then computes metrics for any view of the results: `/code-review`, the full board, the default board, any subset of members, and any label filter.

Judging runs on account A only, because the local endpoint key is personal (PLAN 5.1).

## Commands

Run from the repo root. `uv sync` once first.

```bash
uv run python -m eval.scoring judge   --dataset martian --judge deepseek-flash --split dev
uv run python -m eval.scoring report  --dataset martian --judge deepseek-flash --split dev
uv run python -m eval.scoring members --dataset martian --judge deepseek-flash --split dev
uv run python -m eval.scoring subsets --dataset martian --judge deepseek-flash --split dev
uv run pytest
```

- **`judge`** makes the LLM calls: extraction, dedup, and one judge call per pair of golden comment and candidate. Every answer is cached in `eval/cache/llm.sqlite`, so re-running after an error repeats only the calls that failed. `--whole` also judges each run with all findings extracted as one comment (see the method below).
  - **Pacing for the shared endpoint (PLAN 5.1):** at most `--rate` calls a minute (default 10), `--concurrency` at a time (default 1).
  - On a 429 or a lost connection, it pauses (1 minute, doubling up to 15) and repeats the call.
  - It stops after `--max-wait` minutes (default 180) without success; re-run to continue.
- **`report`** makes no LLM calls. It prints precision, recall, F1 with its bootstrap standard error, F2, usage per run, and the paired bootstrap for chosen pairs of views. `--categories` adds recall per golden category, and `--profile strict|core|all` picks Martian's profile (default `core`). With several judges (`--judge a,b,c`), it scores the panel.
- **`members`** prints, per member: golden comments found, found by no other member, and the false positives it adds. Then one **card** per member (PLAN 6.5; `--member <id>` prints only that member's):
  - the runs it ran in;
  - the golden comments in its own categories, found by the member against `/code-review`;
  - unique golden comments (found by no other member);
  - its marginal recall (the primary member metric) and its contribution to board F1, each with a paired bootstrap;
  - its usage share.

  It also gives finding precision per label, the wrongful-refutation rate, and the golden comments that adjudication lost (PLAN Phase 4).
- **`subsets`** scores every subset of members and applies the selection rule of PLAN Phase 4.
- **`gate2`** applies Gate 2 as pre-registered on 2026-11-04 (PLAN Phase 3): full-board recall (`board:full:cp`) above `/code-review`'s, with 2 repeats per PR for both. It prints PASS, FAIL, or NOT DECIDED, with precision, F1, the paired-bootstrap CIs, and the grouping loss.
- **`merge`** and **`gate3`** apply Gate 3 (PLAN Phase 3): `merge --pairs 1,2 3,4` merges two judged `/code-review` runs into one `code-review-x2` record; `gate3` compares A2 (`board-cr:full:cp:issues`) with A1 (`code-review-x2`) on recall, with one run's F1 as the guardrail.
- **`gate4`** applies Gate 4 (PLAN Phase 3): false positives per PR, B2 (`board-default:full:cp:issues`) minus B1 (`board-none:full:confirmed:issues`), decide with a paired bootstrap; B2's recall must be at least 90% of the recall of B1's own `/code-review` sessions judged alone (`code-review-b1:issues`). Judge with `--variants board-none,board-default,code-review-b1` first. It prints BOARD WINS, CHECKING IS ENOUGH, NO DECISION, or NOT DECIDED (too few repeats).
- **`gate1`** (J3) runs Martian's full pipeline on the stored `claude-code` reviews with a published judge (`--judge openrouter:anthropic/claude-sonnet-4.5`) and compares each golden comment's verdict with Martian's published ones (PLAN Phase 3). Judges named `openrouter:<model id>` use `OPENROUTER_API_KEY` from `.env`.
- **`gate1b`** (J3) re-judges the stored reviews of 7 published tools with a local judge, compares its tool ranking with Martian's published judges, then asks 200 cached prompts again as a repeat check (PLAN Phase 3). Needs `--martian-dir`, the pinned Martian clone's `offline/` folder.

### Views

| View | Candidates |
|---|---|
| `code-review` | `/code-review`'s findings |
| `board:full:cp` | all members; findings labeled CONFIRMED or PLAUSIBLE (the Gate 2 view) |
| `board:default:cp` | the members in `plugin/skills/review/roster.json` → `default` |
| `board:edge,intent:confirmed` | any list of members; CONFIRMED only |
| `board:full:raw` | every finding, before adjudication |
| `board:full:cp:ungrouped` | a diagnostic: every finding counts, not one per board group. The difference to `board:full:cp` is the grouping loss |
| `...:issues` | per issue: every finding in the view is kept, and each Martian dedup group counts once (Gates 3 and 4) |
| `board-cr:...`, `board-none:...`, `board-default:...` | the same over Gate 3's A2 and Gate 4's B1 and B2 runs; `full` is every member present |
| `code-review-b1` | the `/code-review` session inside each `board-none` run, judged as a `/code-review` run |
| `code-review@whole`, `board-full@whole` | the check described in the method, step 2 |

### Test PRs

`--split test` and `--split all` refuse to run without `--release "<why>"`, and every use is appended to `eval/reports/test-split-log.md` (PLAN 6.6). Use `--split dev` for everything else.

## Method

1. **Martian's code, unchanged.** `martian/` holds byte-identical copies of Martian's extraction, dedup, and judge steps and its profile definitions, pinned to the benchmark commit. A test checks their SHA-256. The wrapper calls their prompts and LLM functions, and scores with their `evaluate_review`.
2. **Both variants are scored the same way: one candidate per finding.** Both variants report structured findings: the board in its JSON, and `/code-review` through its `ReportFindings` tool call, which the scorer reads from `stream.jsonl`. Each finding becomes one comment, its location plus its one-sentence statement (the board's `claim`, `/code-review`'s `summary`). Evidence, failure scenarios, and fixes are left out for both. That comment is the candidate as it stands, so every candidate keeps the member and label it came from.
   - **No per-finding extraction.** A finding is already one issue: members report one finding per root cause, and `/code-review`'s findings are separate items. On the smoke repo, extracting findings one at a time split compound sentences (one "four behaviors are untested" finding became five candidates, four of them false positives), while extracting the whole report gave exactly one candidate per item. Martian too scores the raw comments when nothing was extracted.
   - **The board counts once per group.** Its report shows one item per group of duplicate findings, so only each group's first finding among the view's members is scored. Without this, five members reporting one bug would add four false positives.
   - This is what makes subset scoring possible. Martian itself extracts a tool's whole review at once. `judge --whole` scores that way too (one item per board group, CONFIRMED + PLAUSIBLE), as a check. If the two disagree on the benchmark, report both.
   - At `medium`, `/code-review` is told not to call `ReportFindings` (Sonnet follows this; the campus model didn't). Its reply then lists findings as ``N. `file:line`: summary`` with a `Failure scenario:` line, and the scorer parses that list. Only if neither exists is the reply (`final.md`) extracted whole.
3. **Dedup once per run.** Martian's dedup groups are computed on all candidates of a run, and each view uses the groups restricted to its own candidates (PLAN 6.2).
4. **Every view comes from one set of verdicts.** The judge answers every pair of golden comment and candidate once. A view is scored by running Martian's `evaluate_review` on the view's candidates, with those recorded answers in place of new calls. Testing a new member or a different roster costs no extra judge calls.
5. **Metrics follow Martian's `score_profiles`.**
   - True positives and false negatives count only golden comments whose category is in the profile. False positives count every candidate that matched nothing.
   - Counts are summed over PRs. A PR's repeats are averaged first, so each PR has the same weight.
6. **Bootstrap.** Resample PRs with replacement, 2,000 times by default. The paired version uses the same resamples for both views, over the PRs both have. The p-value is two-sided, and is `n/a` when the resamples don't vary, as with a single PR.
7. **Judge panel.** A golden comment counts as found when a majority of judges matched it. False positives are the median of the judges' counts.
8. **Member and label statistics** count a candidate as matched when the judge matched it to any golden comment in the profile. This skips the tie-break in `evaluate_review`, which credits only each golden comment's best candidate, so these are diagnostics, not leaderboard numbers.

## Files

```
eval/scoring/
  __main__.py      command line
  data.py          datasets, run results, the findings of each variant, rendering
  llm.py           cached calls through Martian's LLM classes
  pipeline.py      judged records: candidates, dedup groups, verdicts
  views.py         views, scoring a record with Martian's evaluate_review, the judge panel
  metrics.py       profiles, F-beta, recall per category, bootstrap
  board.py         member and label statistics, the default-board selection rule
  martian/         vendored Martian code (do not edit; see VENDORED.md)
  tests/           offline tests, with no endpoint calls
eval/results/<dataset>/judged/<judge>/<variant>/<pr_id>/r<repeat>.json   judged records (git-ignored)
eval/results/<dataset>/judged/<judge>/sessions.jsonl                     what the endpoint reported at each session
eval/cache/llm.sqlite                                                    every judge-model answer, with its prompt (git-ignored)
```

## Checks done

- **Published numbers reproduced.** From Martian's stored evaluations, `metrics.py` reproduces the published precision, recall, F1, and F2 of 180 of the dashboard's 189 rows (3 judges × 21 tools × 3 profiles) exactly. The other 9 rows belong to one tool (`gitar`) that has no entries in the stored evaluations.
- **Offline tests:**
  - subset views match `evaluate_review` run directly;
  - dedup groups restricted correctly;
  - the board JSON repaired (missing `{`, code fence);
  - the rejected `ReportFindings` call skipped;
  - the panel, the bootstrap, and the selection rule.
- **Smoke dataset** (`eval/datasets/smoke`): the planted-bug repository as one PR, for running the whole pipeline on real output. Its numbers say nothing about review quality.
