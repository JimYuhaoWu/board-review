# PLAN v2, Phase C at `medium`: the frozen add-on on held-out PRs (question 3, reduced)

**Result: FAIL.** The frozen add-on (keep the issues all 5 of 5 runs found) cuts false positives per PR from 4.90 to 1.84 (−3.06, 95% CI −3.47 to −2.67), but keeps 88.5% of one native run's recall (63.4% against 71.6%), below the 90% floor. Reported as measured, with no tuning (PLAN-v2, Phase C). The judge check passed (96.7%), so the result is final.

- **PRs:** the 58 held-out test PRs in `eval/datasets/<dataset>/phase-c.json`, 28 Qodo and 30 Martian, pooled as in Phase B. No gate or phase had scored them.
- **Runs (Y14):** native `/code-review medium --max-findings all`, Claude Code 2.1.317, `claude-sonnet-5-5`, session effort `medium`, account B, no board plugin. 5 runs per PR, 290 in all, every one `ok`, at commit `d8946bcc563e`; no subagents and no Task or Agent calls; $18.71.
- **The add-on:** frozen in Phase B: n = 5, m = 5, matched by one `claude-sonnet-5-5` call per PR (effort `medium`, `MATCH_PROMPT` sha256 `d8e289503446…`), $1.71 for the 58 PRs.
- **Scoring:** judge `deepseek-flash`, Core profile, as in Phase B (no failed calls). The native baseline is each of the same 5 runs scored alone, averaged; the bootstrap pairs over PRs (2,000 draws).
- **Decision rule (fixed before any run):** false positives per PR, add-on − native, 95% CI below 0; and the add-on's recall at least 90% of the native runs' (point estimate). Both: PASS; otherwise FAIL.

## Decision

| | Precision | Recall | FP per PR | ΔFP (95% CI) | Recall vs one run | Cost per PR |
|---|---|---|---|---|---|---|
| One native run | 30.2% | 71.6% | 4.90 | | 100% | $0.06 |
| Add-on, 5 of 5 | 50.5% | 63.4% | 1.84 | −3.06 (−3.47 to −2.67) | **88.5%** (needed 90%) | $0.35 |

- False positives: met. Recall: not met. **FAIL.**
- **By dataset** (reported, not decided): Qodo keeps 92% of recall (−2.41 FP per PR, CI −2.81 to −2.01); Martian keeps 85% (−3.66, CI −4.26 to −3.11). Phase B's development PRs kept 92% pooled.
- **No re-selection.** Phase B's selection rule, applied to these test PRs, would pick 4 of 4 (91% of recall, −2.80 FP per PR). That is a description of the test data, not a result: choosing the configuration here would tune on the test split. A different m or n is a new plan, tested on new PRs.
- **What holds:** the precision gain replicates on held-out PRs (50.5% against 30.2%, about two thirds of the false positives gone). The recall cost is larger than in development: 8.2 points against 6.1.

## Judge check

Jim labeled 60 of the judge's verdicts on Phase C's Qodo runs blind (`python -m eval.scoring judge-check`, seed 20261122): 30 match verdicts, and 30 no-match verdicts where the finding is in the golden comment's file (pairs from other files are trivially different issues). **58 of 60 agree (96.7%; needed 85%).** Judge match: Jim yes 29, no 1. Judge no match: Jim yes 1, no 29. Jim reported that many pairs were hard to call, so the margin is softer than the number.

## Pooled: 58 PRs

Every m of n up to 5, for reference; only 1 of 1 and 5 of 5 decide.

| Keep issues found by | Precision | Recall | FP per PR | ΔFP vs one run (95% CI) | Recall vs one run | Cost per PR |
|---|---|---|---|---|---|---|
| 1 of 1 | 30.2% | 71.6% | 4.90 |  | 100% | $0.06 |
| 1 of 2 | 26.1% | 78.4% | 6.58 | +1.68 (+1.50 to +1.87) | 109% | $0.16 |
| 2 of 2 | 39.7% | 69.1% | 3.11 | -1.79 (-1.99 to -1.61) | 97% | $0.16 |
| 1 of 3 | 23.8% | 80.6% | 7.67 | +2.77 (+2.47 to +3.07) | 113% | $0.22 |
| 2 of 3 | 34.8% | 77.1% | 4.29 | -0.61 (-0.74 to -0.48) | 108% | $0.22 |
| 3 of 3 | 44.6% | 66.9% | 2.47 | -2.43 (-2.71 to -2.16) | 93% | $0.22 |
| 1 of 4 | 22.2% | 81.7% | 8.51 | +3.61 (+3.19 to +4.03) | 114% | $0.29 |
| 2 of 4 | 31.8% | 79.8% | 5.07 | +0.17 (-0.01 to +0.35) | 111% | $0.29 |
| 3 of 4 | 39.0% | 75.2% | 3.48 | -1.42 (-1.59 to -1.24) | 105% | $0.29 |
| 4 of 4 | 47.9% | 65.1% | 2.10 | -2.80 (-3.16 to -2.47) | 91% | $0.29 |
| 1 of 5 | 21.0% | 82.6% | 9.21 | +4.31 (+3.78 to +4.84) | 115% | $0.35 |
| 2 of 5 | 29.9% | 81.4% | 5.67 | +0.77 (+0.51 to +1.06) | 114% | $0.35 |
| 3 of 5 | 36.1% | 78.5% | 4.12 | -0.78 (-0.97 to -0.58) | 110% | $0.35 |
| 4 of 5 | 42.4% | 74.4% | 3.00 | -1.90 (-2.10 to -1.69) | 104% | $0.35 |
| 5 of 5 | 50.5% | 63.4% | 1.84 | -3.06 (-3.47 to -2.67) | 88% | $0.35 |

## Qodo alone: 28 PRs

| Keep issues found by | Precision | Recall | FP per PR | ΔFP vs one run (95% CI) | Recall vs one run | Cost per PR |
|---|---|---|---|---|---|---|
| 1 of 1 | 36.4% | 70.5% | 3.87 |  | 100% | $0.06 |
| 1 of 2 | 31.1% | 75.2% | 5.24 | +1.37 (+1.16 to +1.58) | 107% | $0.15 |
| 2 of 2 | 47.2% | 69.1% | 2.43 | -1.44 (-1.65 to -1.24) | 98% | $0.15 |
| 1 of 3 | 28.2% | 76.8% | 6.14 | +2.26 (+1.93 to +2.63) | 109% | $0.21 |
| 2 of 3 | 41.2% | 74.9% | 3.36 | -0.51 (-0.67 to -0.35) | 106% | $0.21 |
| 3 of 3 | 52.3% | 67.4% | 1.93 | -1.94 (-2.22 to -1.65) | 96% | $0.21 |
| 1 of 4 | 26.4% | 77.7% | 6.82 | +2.95 (+2.47 to +3.44) | 110% | $0.27 |
| 2 of 4 | 37.6% | 76.4% | 3.98 | +0.11 (-0.11 to +0.32) | 108% | $0.27 |
| 3 of 4 | 45.9% | 73.6% | 2.73 | -1.14 (-1.37 to -0.92) | 105% | $0.27 |
| 4 of 4 | 55.7% | 65.9% | 1.65 | -2.22 (-2.57 to -1.87) | 94% | $0.27 |
| 1 of 5 | 25.0% | 78.4% | 7.39 | +3.52 (+2.92 to +4.14) | 111% | $0.33 |
| 2 of 5 | 35.6% | 77.3% | 4.39 | +0.52 (+0.22 to +0.83) | 110% | $0.33 |
| 3 of 5 | 42.1% | 76.1% | 3.29 | -0.59 (-0.84 to -0.31) | 108% | $0.33 |
| 4 of 5 | 49.6% | 72.7% | 2.32 | -1.55 (-1.84 to -1.28) | 103% | $0.33 |
| 5 of 5 | 58.2% | 64.8% | 1.46 | -2.41 (-2.81 to -2.01) | 92% | $0.33 |

## Martian alone: 30 PRs

| Keep issues found by | Precision | Recall | FP per PR | ΔFP vs one run (95% CI) | Recall vs one run | Cost per PR |
|---|---|---|---|---|---|---|
| 1 of 1 | 25.8% | 72.9% | 5.86 |  | 100% | $0.07 |
| 1 of 2 | 22.6% | 81.7% | 7.83 | +1.97 (+1.72 to +2.25) | 112% | $0.17 |
| 2 of 2 | 34.1% | 69.2% | 3.74 | -2.12 (-2.41 to -1.85) | 95% | $0.17 |
| 1 of 3 | 20.7% | 84.6% | 9.10 | +3.24 (+2.84 to +3.71) | 116% | $0.24 |
| 2 of 3 | 30.1% | 79.4% | 5.16 | -0.70 (-0.90 to -0.51) | 109% | $0.24 |
| 3 of 3 | 38.5% | 66.3% | 2.97 | -2.89 (-3.31 to -2.52) | 91% | $0.24 |
| 1 of 4 | 19.3% | 86.0% | 10.09 | +4.23 (+3.69 to +4.86) | 118% | $0.30 |
| 2 of 4 | 27.7% | 83.3% | 6.09 | +0.23 (-0.06 to +0.53) | 114% | $0.30 |
| 3 of 4 | 34.0% | 76.9% | 4.19 | -1.67 (-1.92 to -1.45) | 106% | $0.30 |
| 4 of 4 | 41.7% | 64.3% | 2.51 | -3.35 (-3.86 to -2.89) | 88% | $0.30 |
| 1 of 5 | 18.2% | 86.9% | 10.90 | +5.04 (+4.35 to +5.83) | 119% | $0.37 |
| 2 of 5 | 25.9% | 85.7% | 6.87 | +1.01 (+0.60 to +1.43) | 118% | $0.37 |
| 3 of 5 | 31.6% | 81.0% | 4.90 | -0.96 (-1.24 to -0.70) | 111% | $0.37 |
| 4 of 5 | 37.0% | 76.2% | 3.63 | -2.23 (-2.51 to -1.95) | 105% | $0.37 |
| 5 of 5 | 44.1% | 61.9% | 2.20 | -3.66 (-4.26 to -3.11) | 85% | $0.37 |
