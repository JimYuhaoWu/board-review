# Consensus review: plan v2

Status: **approved 2026-11-08 (Jim); Phase A done, Phase B next.** Yara reviews it after the merge; changes come as pull requests, before any Phase B run.

[PLAN.md](PLAN.md) (v1) is the record of the board project, closed after Gate 4. Its fixed settings (5.1), pipeline (6), usage limits (7) and way of working (9) still apply unless this plan changes them.

## 1. Goal

Find the minimal human supervision that automated code review, and later automated code revision, needs.

The first step is an **add-on to native `/code-review`** that raises its precision without losing its recall, in every mode that runs without a human. Precision matters because the revising agent, the next step, acts on every finding it gets: each false positive costs a wrong edit, or a human to stop it.

## 2. What v1 showed

- **The board did not beat `/code-review`.** Gates 2–4 never met their pre-registered rules. In Gate 4, the default board had fewer false positives per PR than `/code-review` checked by the board's verifier (0.94 against 1.35), but its recall fell below the floor (63.2% against the 65.7% needed). The judge check of Gate 4 was not done; Jim closed the gate without it (report: `eval/reports/gate4-qodo-deepseek-flash.md`).
- **Its precision came from agreement.** In Gate 4's default board, an issue found by two or more members was about 85% precise, one found by a single member about 12%. The verifier's labels added little beyond that: B2's PLAUSIBLE findings were 61% precise, its CONFIRMED 70%.
- **The same signal exists between `/code-review` runs.** Two independent runs agree on about half their issues; those issues carry nearly all the recall, and issues found by one run only are about 90% wrong (Phase A).

v1's design principle "specialist members, no voting" (PLAN 2) is what the data argue against: independent samples agreeing is the best precision signal measured so far.

## 3. The add-on

1. Run native `/code-review` **n times**, independently, at the user's mode and settings.
2. A **matching step** groups findings that describe the same issue. It is one model call, part of the add-on, and its errors count against the add-on.
3. Keep an issue found by **at least m of the n runs**: an intersection of the runs when m = n, otherwise the union of the intersections of every m runs.
4. **Report** each kept issue with all its findings from the agreeing runs, grouped. Phases B and C score this output, per issue: a kept issue counts as found when any of its findings matches a golden issue, as in Phase A. Reporting one wording per issue would score lower (Phase A caveats).

There is **no verifier** (question 2). Question 1 chooses n and m; question 3 tests the result in every mode.

## 4. Questions

1. **How many repeats?** Which (m, n) gives the fewest false positives per PR while keeping recall, and is the step from 2 to 3 runs worth its cost?
2. **What should the verifier do?** **Closed (Jim, 2026-11-09): the verifier is abandoned, replaced by intersections of `/code-review` runs.** In Phase A it cost as much as 4–5 runs; the roles that kept recall removed few false positives, and the role that removed many lost recall below a 90% floor. It was not tuned, and this was not a head-to-head test: the decision rests on cost and on keeping the add-on simple.
3. **Does the add-on improve the original in every mode?** The modes are `/code-review`'s effort levels `low`, `medium`, `high`, `xhigh` and `max`: the level argument (`/code-review high`). The session's own `--effort` stays at `medium` (v1 5.1), so a mode changes one setting only. **Ultra is out of scope**: it runs in the cloud and needs a human to launch it, which defeats the goal.

## 5. Phases

### Phase A: preview from existing runs (done, exploratory)

Re-scored, with no new review runs, from runs on test PRs already used by Gates 3 and 4: so it shapes the plan and decides nothing. Per issue, Core profile, `deepseek-flash`. Agreement here uses the judge's own dedup, an upper bound for the add-on's matching step. Script outputs are in `eval/reports/v2-preview.md`.

Core profile, false positives per PR (FP) and recall:

| Configuration | PRs | Precision | Recall | FP per PR | Cost per PR |
|---|---|---|---|---|---|
| one run (native) | Gate 3 | 36.2% | 65.4% | 3.51 | $0.06 |
| 2 of 2 runs | Gate 3 | 49.8% | 68.0% | 2.08 | $0.12 |
| 3 of 3 runs | Gate 3 | 56.3% | 69.2% | 1.64 | $0.18 |
| 4 of 4 runs | Gate 3 | 60.7% | 69.9% | 1.38 | $0.24 |
| one run (native) | Gate 4 | 37.9% | 73.0% | 3.79 | $0.07 |
| one run + verifier, CONFIRMED (v1's B1) | Gate 4 | 56.1% | 54.6% | 1.35 | $0.37 |
| 2 of 2 runs | Gate 4 | 57.7% | 73.7% | 1.71 | $0.13 |
| 2 of 2, verifier drops REFUTED | Gate 4 | 58.9% | 73.7% | 1.62 | $0.75 |
| 2 of 2, verifier keeps CONFIRMED | Gate 4 | 71.4% | 59.2% | 0.75 | $0.75 |

- **Question 1:** unanimity (m = n) is best at every n and keeps one run's recall. Each extra run helps less (−1.43, −0.44, −0.26 FP per PR), but the curve has not flattened at n = 4. Majority rules buy recall with precision.
- **Question 2:** the verifier costs as much as 4–5 runs. Dropping REFUTED is nearly free in FP (−0.08); keeping CONFIRMED only halves FP but loses 14.5 recall points, below a 90% floor; rescuing single-run issues adds more FP than recall.

The preview's answer: all of n runs, n ≥ 3. After the preview, Jim dropped the verifier (question 2).

### Phase B: development (question 1)

- **PRs:** the 24 Qodo and 15 Martian development PRs (the pilot PRs are among them). No test PR.
- **Runs:** native `/code-review medium --max-findings all` (v1 5.1 settings), **k = 5** independent runs per PR, 195 in all. The 68 existing runs on these PRs have matching settings and are reused (2 per PR on 24 Qodo and 10 Martian PRs); 127 are new (3 more on those 34 PRs, 5 on the other 5 Martian PRs). Runs from Gates 3 and 4 are on test PRs and are not reused. Every subset of n ≤ 5 runs is scored, so one batch answers question 1.
- **Matching step:** built and tuned here, as a `claude -p` session on the review's model and settings (`claude-sonnet-5-5`, effort `medium`): what a user of the add-on would run, and a model other than the judge, so the add-on and the scoring don't share errors. It is checked against the judge's dedup and a model panel before question 1 is scored with it; if it falls short, question 1 is not scored with it.
- **Matching check, no human labels (Jim, 2026-11-09), fixed before any tuning:**
  - **Panel:** `deepseek-flash`, `glm-flash` and `Qwen` on the local endpoint. Each answers one question per pair, "would fixing one fix the other?", without seeing the matcher's answer; the label is what at least 2 of 3 say.
  - **Sample:** 200 pairs of findings from different runs on development PRs, drawn from the final matcher's output: 100 it linked and 100 near-misses it didn't (the same file, or linked by the judge's dedup).
  - **Pass:** the matcher agrees with the panel on at least 90% of each half separately, so a matcher that links nothing cannot pass.
  - **Panel health:** all three models agree on at least 70% of the pairs. Below that the check is inconclusive, and question 1 is not scored with this matcher until the cause is understood.
  - **Limit:** this measures agreement with three other models, not ground truth; an error all three share goes unseen.
- **Selection rule, fixed before any Phase B scoring:**
  - Recall floor: at least 90% of one run's recall on the same PRs (point estimate).
  - Among configurations that meet it, the fewest false positives per PR; then the cheapest within one bootstrap standard error of it. A configuration's cost includes its matching step, which Phase A's costs leave out.
- **Output:** one fixed add-on (n, m, matching prompt and model), frozen before Phase C, and a figure of false positives per PR against recall for every (m, n) up to n = 5, with cost shown.

### Phase C: confirmation in every mode (question 3)

- **PRs:** the 28 Qodo test PRs no gate has used (none from redis: Gates 3 and 4 used all of its test PRs), and the 30 Martian test PRs, pooled into 58 PRs as Phase B pools its two datasets; each dataset is also reported alone. Logged as usual (v1 6.6).
- **Per mode** (`low`, `medium`, `high`, `xhigh`, `max`): n native runs per PR at that mode, with n from Phase B. The add-on is the frozen add-on over those n runs; the native baseline is each of the same runs scored alone, averaged. Fully paired, and half the runs of 2 add-on repeats.
- **Every run passes its level and `--max-findings all` explicitly:** `/code-review` remembers the last choice in the config folder, which parallel runs share.
- **Decision per mode:** false positives per PR, add-on − native, paired bootstrap over PRs; the CI must be below 0 after a Holm correction over the 5 modes, and the add-on's recall at least 90% of the native runs' (point estimate).
- **Claim:** "the add-on improves `/code-review` in every mode" only if all 5 modes pass; otherwise the modes that pass are reported as such.
- **Judge check:** Jim's 60-pair blind check is required before the result is final (v1's rule; below 85% agreement, NOT DECIDED). Gate 4 skipped it; Phase C doesn't.

## 6. Cost

- One `/code-review` run at `medium` is about $0.07 (Gate 4).
- Phase B: 127 new runs on 39 PRs ≈ $8–9. Account B; judging on account A.
- Phase C: the cost per mode is unknown above `medium`. Yara's smoke test (Y13) measures one run per mode on 2 PRs before the budget is agreed.
- **Weekly limit.** v1 section 7 plans around the 5-hour window, but Gate 4's overnight batch (about $40) stopped at account B's weekly limit. Phase C is n × 58 × 5 runs, 870 at n = 3; at `max` that may be hundreds of dollars. Its size is set from Y13's costs against the weekly limit before any Phase C run: `max` may run on fewer PRs, or be reported without deciding.

## 7. Open

- Whether `max` fits the budget, after Y13.
