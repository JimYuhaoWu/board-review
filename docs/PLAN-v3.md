# Review, then revise: plan v3

Status: **approved 2026-11-20 (Jim); R0 and R1 done (2026-11-22); R2 done (2026-11-23): PASS, with C5's bugs fixed at 90.6% of N1's (floor 90%); fix judge check 37 of 40 (92.5%).** Changes come as pull requests.

[PLAN-v2.md](PLAN-v2.md) is the review add-on: native `/code-review` run 5 times at `medium`, matched by one Sonnet call, issues kept only when all 5 runs found them. Its Phase C test at `medium` runs in parallel (TASKS Y14); this plan does not wait for it. v1's fixed settings (PLAN 5.1), pipeline (6), usage limits (7) and way of working (9) still apply unless this plan changes them.

## 1. Goal

The overall goal is still to find the minimal human supervision that automated review and revision need. v3 takes the next step: a **revising agent** that edits the PR from a review's findings, with no human between the two. It measures what a human would still have to do afterwards:

- **wrong edits** to revert: changes that fix no planted issue;
- **missed bugs** to fix by hand: planted bugs the revision left in place.

The add-on's case was that each false positive becomes a wrong edit (v2, 1). v3 tests that claim directly.

## 2. Ground truth: Qodo's clean commit

Each Qodo PR's head is the upstream PR plus one commit, "update pr", that injects its issues (`eval/datasets/qodo/SOURCE.md`). Checked on 2026-11-19 through the GitHub API for all 100 PRs: the last commit is that one, with a single parent, and it is the `head` in `prs.jsonl`. So:

- `head^` is the PR **without** the planted issues: a reference fix for every planted issue at once;
- `head^..head` is the **injection diff**: each planted issue sits in its hunks, and the golden comments give the file and lines.

Martian's bugs are historical, with no clean reference commit, so v3 uses **Qodo only** (Jim, 2026-11-20). Martian could be added later, judged against the golden comment alone.

## 3. The reviser

- One `claude -p` session per (PR, input, repeat), in a fresh worktree at `head`. It uses the review's model and settings: `claude-sonnet-5-5`, effort `medium` (v1 5.1).
- **Tools:** Read, Grep, Glob, Edit, Write and read-only `git`. No network and no `git commit`.
- **Inputs:** the PR intent and a findings file, both outside the worktree.
- **Outputs:**
  - **The edits:** `git diff` of the worktree against `head`.
  - **A report:** one line per finding, `fixed` or `declined` with a reason.
- **The reviser may decline a finding it judges wrong.** Declining is the cheapest filter a fully automated pipeline has, and v3 measures how well it works (question 3). Decided: yes (Jim, 2026-11-20).
- **No clean history.** The reviser's repository holds base → head as one commit, so `head^`, the answer, is out of reach (TASKS, "The reviser (Y15)"). Review runs had the full history; 2 of the 216 Qodo `/code-review` runs looked at commit history, and neither read the injection commit's diff.
- The prompt is fixed before R1 and may change only on development PRs (R1). It is frozen before R2.

## 4. Questions

1. **Does the add-on's precision carry through to revision?** Compare the reviser fed the add-on's findings with the reviser fed one native run's findings. Wrong edits per PR decide. Planted bugs fixed per PR must stay at least 90% of the native input's.
2. **How good is the reviser on its own?** Feed it the golden functional bugs (an oracle input). This gives its fix rate with perfect findings, and the wrong edits it makes even with perfect findings (its own noise floor).
3. **Does declining filter false positives?** On the native input: the decline rate on false-positive findings against the rate on true ones. Findings are labeled true or false by the existing judged records.

## 5. Inputs, per PR

| Input | Findings given to the reviser | Source |
|---|---|---|
| **N1**, native | one `/code-review medium` run, all its findings | Phase B / Phase C runs, repeat k feeds revision repeat k |
| **C5**, the add-on | the issues 5 of 5 runs kept, each with all its findings' wordings | the consensus records |
| **G**, oracle | the PR's functional golden comments (Core profile) | `golden.jsonl` |

No new review runs are needed: Phase B has 5 native runs on all 24 Qodo development PRs, and Phase C will have 5 on its 28 Qodo test PRs.

## 6. Scoring (account A)

- **Hunks.** Each revision diff is split into hunks. A hunk is **on target** if it overlaps a line the injection diff changed (±3 lines). Otherwise it is **off target**.
- **Planted bug fixed** (Core: the bug category only, as in v1 and v2). This is decided in two steps:
  - **Exact revert:** the revision restores `head^`'s text in that bug's injected hunk, so it is fixed. No judge is needed.
  - **Otherwise, a fix judge decides:** `deepseek-flash` on the local endpoint, at the usual judging rate. The judge sees the golden comment, the bug's injected hunk, and the revision's diff in that file, and answers FIXED or NOT FIXED.
  - **Check on the judge:** before R1's numbers are read, Jim checks it blind on 40 bug–revision pairs. At least 85% agreement is needed (v1's rule), and below that R1 is NOT DECIDED.
- **Wrong edits per PR:** the off-target hunks, the number of edits a human would have to review and probably revert. Off-target edits are not all harmful: a reviser can fix a real upstream bug. The count is therefore an upper bound on damage and a direct measure of review burden. A build or test check is deferred (Jim, 2026-11-20): the 8 repositories need 8 toolchains.
- **Also reported:**
  - planted rule issues fixed (the All profile);
  - lines changed;
  - the cost per PR of review plus revision;
  - the reviser's action on each finding by its label (true, other, false): a fixed false finding is a wrong edit even when it lands within 3 lines of an injected block, where the hunk count misses it (smoke test, prefect-1).

## 7. Phases

### R0: build

- **Y15 (account B):**
  - a runner variant `revise` with `--input N1|C5|G`, writing the layout v1 uses under `eval/results/qodo/revise-<input>/<pr>/r<k>/`;
  - the findings files, built from records Jim sends (Yi's side never reads golden comments, so G's file comes from account A);
  - a smoke test on 2 development PRs that measures the cost per run.
- **J10 (account A):**
  - the findings files for each input;
  - the injection diff, hunk overlap, exact-revert check and fix judge;
  - the scoring command and its tests.

### R1: development (exploratory)

- **PRs:** the 24 Qodo development PRs.
- **Runs:** N1, C5 and G, 2 repeats each: 144 reviser runs.
- **What it decides:** nothing. It sets the reviser prompt, which is frozen afterwards.
- **Report:** the three inputs' bugs fixed and wrong edits per PR, and the decline table.

- **Result (2026-11-22):** 144 of 144 runs `ok` (one re-parsed after a parser fix), $11.59. Bugs fixed per PR: N1 2.10, C5 1.85, G 2.42; wrong edits per PR: N1 1.06, C5 0.60, G 0.02. C5 − N1, wrong edits: −0.46 (95% CI −0.96 to −0.04); C5's bugs fixed are 88% of N1's. Fix judge check: 39 of 40 (97.5%). The prompt is frozen unchanged. Report: `eval/reports/r1.md`.

### R2: test

- **PRs:** the 28 Qodo PRs of Phase C. They have been used for review, never for revision. Their 5 native runs come from Y14, so no new review runs are needed.
- **Runs:** N1, C5 and G, 2 repeats each, using the frozen prompt: 168 runs.
- **Decision (question 1):**
  - The difference in wrong edits per PR (C5 minus N1) is tested with a paired bootstrap over PRs, and its 95% CI must be below 0.
  - C5's planted bugs fixed per PR must be at least 90% of N1's (point estimate).
  - If both hold, the result is **PASS**; otherwise it is **FAIL**, reported as measured, with no tuning.
- **Judge check:** the fix judge's blind check is repeated on 40 R2 pairs.
- **Result (2026-11-23): PASS.** 168 of 168 runs `ok`, $14.79. Wrong edits per PR: C5 1.38, N1 1.89; C5 − N1: −0.52 (95% CI −1.04 to −0.11). Bugs fixed per PR: C5 1.89, N1 2.09, so 90.6% (106 of 117 bugs); one fewer C5 fix would have failed. G: 2.36 fixed, 0.38 wrong edits. Fix judge check: 37 of 40 (92.5%); with Jim's three corrections applied, the ratio is 90.5%, still a PASS. Report: `eval/reports/r2.md`.

## 8. Cost

- **Reviews:** none new; they come from Phases B and C.
- **Reviser:** R1 cost $11.59 for 144 runs ($0.08 a run), so R2's 168 runs are about $14, on account B.
- **Judging:** about 3 bugs per PR per run, minus exact reverts. That is at most about 430 calls for R1, roughly half an hour at 15 a minute.

## 9. Decided and later

- **Decided (Jim, 2026-11-20):**
  - The reviser may decline findings.
  - Qodo only.
  - Build and test checks are deferred.
- **Later steps, not in v3:**
  - review again after revising (a closed loop);
  - letting the reviser run tests;
  - stopping a revision before it edits.
