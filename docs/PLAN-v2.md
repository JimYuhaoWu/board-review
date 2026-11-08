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
3. Keep an issue found by **at least m of the n runs**.
4. Optionally, a **verifier** acts on some issues (question 2).

Questions 1 and 2 choose n, m and the verifier's role; question 3 tests the result in every mode.

## 4. Questions

1. **How many repeats?** Which (m, n) gives the fewest false positives per PR while keeping recall, and is the step from 2 to 3 runs worth its cost?
2. **What should the verifier do, compared with what?** Candidate roles: (a) filter the agreed issues; (b) rescue issues only one run found; (c) replace a repeat, at about the same cost. Its comparator is the best (m, n) from question 1 **without** a verifier, on the same runs, and a cost-matched alternative: spending the verifier's cost on more runs.
3. **Does the add-on improve the original in every mode?** The modes are `/code-review`'s effort levels `low`, `medium`, `high`, `xhigh` and `max`. **Ultra is out of scope**: it runs in the cloud and needs a human to launch it, which defeats the goal.

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
- **Question 2:** the verifier costs as much as 4–5 runs and does less than one more run. Dropping REFUTED is nearly free in FP (−0.08); keeping CONFIRMED only halves FP but loses 14.5 recall points, below a 90% floor; rescuing single-run issues adds more FP than recall.

The preview's answer: all of n runs, n ≥ 3; no verifier unless Phase B's tuned verifier, run only on agreed issues, earns its place.

### Phase B: development (questions 1 and 2)

- **PRs:** the 24 Qodo and 15 Martian development PRs (the pilot PRs are among them). No test PR.
- **Runs:** native `/code-review medium --max-findings all` (v1 5.1 settings), **k = 5** independent runs per PR. The existing 2 runs per PR count where their settings match; 3 more are run. Every subset of n ≤ 5 runs is scored, so one batch answers question 1.
- **Matching step:** built and tuned here, on a model other than the judge (so the add-on and the scoring don't share errors). It is checked against the judge's dedup and a hand-labeled sample before question 1 is scored with it.
- **Verifier:** role (a) only, the one the preview leaves open: the board's verifier prompt, run once per PR over the issues all k runs agree on (so every n reuses the same labels). Roles (b) and (c) are dropped (Phase A). Tuning the verifier (its PLAUSIBLE rule, its model) is allowed here and only here.
- **Selection rule, fixed before any Phase B run:**
  - Recall floor: at least 90% of one run's recall on the same PRs (point estimate).
  - Among configurations that meet it, the fewest false positives per PR; then the cheapest within one bootstrap standard error of it.
  - A verifier role is kept only if it lowers false positives per PR by more than one standard error against the chosen (m, n) without it, meets the floor, and beats the cost-matched alternative.
- **Output:** one fixed add-on (n, m, matching prompt and model, verifier role or none), frozen before Phase C.

### Phase C: confirmation in every mode (question 3)

- **PRs:** the 28 Qodo test PRs no gate has used, and the 30 Martian test PRs. Logged as usual (v1 6.6).
- **Per mode** (`low`, `medium`, `high`, `xhigh`, `max`): the frozen add-on against one native run at the same mode, paired on the same runs where possible, 2 repeats.
- **Decision per mode:** false positives per PR, add-on − native, paired bootstrap; the CI must be below 0 after a Holm correction over the 5 modes, and the add-on's recall at least 90% of the native run's (point estimate).
- **Claim:** "the add-on improves `/code-review` in every mode" only if all 5 modes pass; otherwise the modes that pass are reported as such.
- **Judge check:** Jim's 60-pair blind check is required before the result is final (v1's rule; below 85% agreement, NOT DECIDED). Gate 4 skipped it; Phase C doesn't.

## 6. Cost

- One `/code-review` run at `medium` is about $0.07; the board's verifier pass over one run's findings about $0.30, so a verifier costs about as much as four runs (Gate 4).
- Phase B: about 120 new runs on 39 PRs ≈ $9, plus one verifier pass per PR over the agreed issues, about $12. Account B; judging on account A.
- Phase C: the cost per mode is unknown above `medium`. Yara's smoke test measures one run per mode on 2 PRs before the budget is agreed.

## 7. Open

- The matching step's model.
- Martian's role in Phase C: second dataset, or a separate claim.
- Whether `max` fits the budget.
