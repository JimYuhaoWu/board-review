# Closing report (2026-11-23)

The project asked how little human supervision automated code review, and then automated revision, need. It is closed here. Every Qodo and Martian test PR has been used, so no further claim can be tested on this data.

## Answers

1. **A board of specialist reviewers doesn't beat native `/code-review`** (PLAN v1, closed 2026-11-08). Gate 2 failed on recall, and Gates 3 and 4 ended NO DECISION, Gate 4 on the recall floor. The board found fewer bugs (Gate 2: 69.4% recall against 77.1%) at about 9–10 times the cost. Reports: `gate2-*`, `gate3-*`, `gate4-*`.
2. **Running `/code-review medium` 5 times and keeping what all 5 found cuts false positives by about two thirds, but costs too much recall** (PLAN v2). Phase B chose the add-on; Phase C on 58 test PRs failed. False positives per PR fell from 4.90 to 1.84 (−3.06, 95% CI −3.47 to −2.67), but recall kept was 88.5% of one run's, against a 90% floor (Qodo 92%, Martian 85%). Reports: `phase-b.md`, `phase-c-medium.md`.
3. **Fed to a revising agent, the add-on's findings leave a little less work for a human** (PLAN v3, R2 PASS on 28 Qodo test PRs). Wrong edits per PR: −0.52 (95% CI −1.04 to −0.11) against one run's findings. Bugs fixed were 90.6% of one run's (floor 90%): a margin of one bug, which survives Jim's fix-judge corrections (90.5%). Reports: `r1.md`, `r2.md`.

Human work left per PR after review and revision (R2, 28 Qodo test PRs, about 3.1 planted bugs each):

| Findings given to the reviser | Wrong edits to revert | Planted bugs missed | Total |
|---|---|---|---|
| One native run | 1.89 | 1.05 | 2.94 |
| The add-on (5 of 5 runs) | 1.38 | 1.25 | 2.63 |
| Perfect findings (golden comments) | 0.38 | 0.79 | 1.17 |

## What this means

- **Review then revise still needs a human on every PR.** That holds even with perfect findings: the reviser fixes 75% of planted bugs and declines a quarter of the true ones.
- **Better findings help less than expected.** The add-on removes about 0.5 wrong edits per PR, but costs about 5 review runs instead of 1 and misses a few more bugs. The gap from the add-on to perfect findings (2.63 to 1.17) is bigger than the gap from one run to the add-on.
- **The reviser's own declines are a real filter.** It declines 51–67% of false findings and 5–8% of true ones, which is part of why the add-on's recall loss at review shrinks after revision.

## Limits

- Qodo's planted bugs only, for revision; reviews at `/code-review medium` only, with `low`, `high` and `xhigh` untested.
- One reviewer model (`claude-sonnet-5-5`) and one judge (`deepseek-flash`). Jim's blind checks agreed with the judge 92.5–97.5%.
- Wrong edits count off-target change blocks; no build or test was run.

## Not done

Left open if the work restarts on new PRs: review after revising (a closed loop), a reviser that runs tests, and the deferred effort levels of PLAN v2.
