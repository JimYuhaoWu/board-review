# Gate 2 on Qodo: deepseek-flash

Rule (PLAN Phase 3, written 2026-11-05 before any Qodo run): the full board's recall on functional bugs (Core profile, findings labeled CONFIRMED or PLAUSIBLE) is higher than `/code-review`'s. 24 PRs (3 per repository, seed 20261105), 2 repeats per PR and variant, 96 runs on account B, judged by `deepseek-flash` on account A.

**Result: FAIL.**

## Core profile (functional bugs, 72 golden issues)

| View | PRs | Runs | Precision | Recall | F1 |
|---|---|---|---|---|---|
| Full board (`board:full:cp`) | 24 | 48 | 55.9% | 69.4% | 61.9% |
| `/code-review` | 24 | 48 | 40.5% | 77.1% | 53.1% |
| Full board, every finding counted | 24 | 48 | 44.0% | 71.5% | 54.5% |

| Paired bootstrap, board − `/code-review` | Point | 95% CI | p |
|---|---|---|---|
| ΔRecall (decides) | −7.6 | −14.9 to −0.7 | 0.050 |
| ΔPrecision | +15.4 | +7.8 to +23.5 | <0.001 |
| ΔF1 | +8.8 | +1.9 to +16.0 | 0.007 |

Grouping loss: 1.5 golden issues. Board views: CONFIRMED only 59.3 / 61.8 / 60.5; every finding before adjudication 54.9 / 70.1 / 61.6. Cost per run: board $0.81, `/code-review` $0.05.

## All profile (with repository-rule violations)

Board 62.0 / 46.4 / 53.1 against `/code-review` 47.9 / 54.0 / 50.8. ΔRecall −7.6 (−13.7 to −1.5), ΔF1 +2.3 (−3.9 to +8.4).

## Where the recall goes

- Every finding the 12 members reported reaches 70.1% recall, below `/code-review`'s 77.1%. The verifier wrongly refuted 2 of 7 REFUTED findings and lost 1 golden issue. Recall is limited by what the members report, not by adjudication.
- Across all 4,095 rosters, re-scored from the same runs, the highest recall is 70.1%. The best F1 is saboteur, intern, line-reader, boundary (85.6 / 66.0 / 74.5), chosen on these PRs.
- Two `/code-review` runs merged reach 84.7% recall; two board runs 77.8%; one of each 82.6%.

## Judge check

Jim labeled 60 verdicts blind (15 per variant and verdict, Core bugs; no-match items drawn from the golden issue's own file; seed 20261106).

- **First pass: 36 / 60 agree (60.0%).** All 24 disagreements were judge no-match, Jim match. The short sheet showed only each golden issue's title, so comments on the same file looked related.
- **Re-judged** with the full texts and the judge's reasoning (not blind): Jim moved 22 of the 24 to no match and was unsure on 2 (items 22 and 47, where the comment says a normalization "is applied to all types" and the issue is a schema "cleared for all variable types"). **58 / 60 agree (96.7%)**, the 2 unsure counted as disagreements.
- The judge never matched a pair Jim called different. The re-judged figure is above the 85% threshold, so the result stands; the first-pass figure is reported with it because the re-judge saw the judge's reasoning.
