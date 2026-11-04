# Pilot, Gate 2: full board vs `/code-review`

Judge `deepseek-flash` (passed Gate 1b), Martian's Core profile, the 10 pilot PRs (development split), 1 run per PR and variant. Runs: account B, Claude Code 2.1.317, Sonnet 5.5 at `medium`. The board runs are the re-run on the plugin at `6f70144` (#20).

## The rule, fixed before any result (PLAN Phase 3)

> the full board must score a higher F1 than `/code-review` on the pilot PRs, using the development judge, Martian's Core profile, and board findings labeled CONFIRMED or PLAUSIBLE. If it doesn't, stop and rethink the design before spending more usage.

## Result: **Fail**

| View | Precision | Recall | F1 (± SE) | TP | FP | FN | Cost/run |
|---|---|---|---|---|---|---|---|
| `code-review` | 30.8% | 74.4% | **43.5%** ± 3.5% | 32 | 72 | 11 | $0.07 |
| `board:full:cp` (the gate metric) | 32.9% | 58.1% | **42.0%** ± 6.3% | 25 | 51 | 18 | $0.75 |
| `board:full:confirmed` | 34.4% | 51.2% | 41.1% ± 8.3% | 22 | 42 | 21 | |
| `board:full:raw` (including REFUTED) | 32.5% | 58.1% | 41.7% ± 6.2% | 25 | 52 | 18 | |
| `code-review@whole` (check) | 19.6% | 74.4% | 31.1% ± 4.1% | 32 | 131 | 11 | |
| `board-full@whole` (check) | 25.5% | 58.1% | 35.5% ± 6.5% | 25 | 73 | 18 | |

Paired bootstrap over PRs, board − `/code-review`: ΔF1 −1.5 points (95% CI −12.5 to +9.4, p 0.82), Δrecall −16.3, Δprecision +2.1.

The difference is within noise, but the rule asks for a higher F1, and the board's is lower. Per the rule, we stop and rethink before the full runs.

## Where the gap is

**Adjudication is not the cause.** No golden comment found by a member was lost by adjudication (wrongful refutation rate 0.0%), and only 2 of 146 findings were REFUTED. The raw view (every finding, refuted or not) has the same recall as the gate view. Board precision is at least as good as `/code-review`'s. The gap is recall: 25 vs 32 golden comments found.

**Most of the recall gap is security.** Recall per golden category:

| View | api | bug | concurrency | data | doc_defect | security | style | test_gap |
|---|---|---|---|---|---|---|---|---|
| `code-review` | 1/1 | 18/22 | 4/4 | 2/3 | 1/4 | **6/7** | 0/4 | 0/2 |
| `board:full:cp` | 1/1 | 15/22 | 4/4 | 1/3 | 1/4 | **1/7** | 1/4 | 2/2 |

No member raised the 5 missed security comments, so this is a coverage gap, not an adjudication one.

**One PR carries most of it.** Per PR (TP / FP / FN, Core):

| PR | `code-review` | board |
|---|---|---|
| cal_dot_com-14740 | 4/10/2 | 4/3/2 |
| cal_dot_com-7232 | 3/9/0 | 2/6/1 |
| discourse-1 | 3/4/1 | 2/3/2 |
| **discourse-4** | **7/12/1** | **3/11/5** |
| grafana-79265 | 4/6/1 | 4/4/1 |
| grafana-90045 | 3/6/0 | 3/5/0 |
| keycloak-37429 | 1/8/2 | 0/5/3 |
| keycloak-37634 | 2/8/2 | 1/7/3 |
| sentry-77754 | 2/5/0 | 1/1/1 |
| sentry-93824 | 3/4/2 | 5/6/0 |

`discourse-4` has 5 of the 7 security comments. `/code-review` found 4 of them (the fifth, a referer header put unescaped into JavaScript, neither variant found). The board found none, though the Saboteur raised two other injection findings there. The four it missed:

- a server-side fetch of a URL from a site setting, with no validation (SSRF);
- an origin check with `indexOf`, which a look-alike domain passes;
- `X-Frame-Options: ALLOWALL`, which disables clickjacking protection;
- `postMessage` with the full referrer URL as the target origin, instead of the origin.

Without `discourse-4`, the board leads: F1 45.4% vs 41.7%. This slice was chosen after seeing the result, so it **does not change the gate**. It only says where to look.

**Members** (labels CONFIRMED + PLAUSIBLE):

| Member | Golden found | Found by no other member | Candidates | Precision |
|---|---|---|---|---|
| intent | 15 | 2 | 21 | 61.9% |
| edge | 15 | 1 | 27 | 51.9% |
| intern | 14 | 0 | 23 | 56.5% |
| nitpicker | 13 | 1 | 34 | 35.3% |
| contract | 8 | 0 | 15 | 46.7% |
| concurrency | 6 | 1 | 11 | 54.5% |
| **saboteur** | **2** | 2 | **7** | 28.6% |
| perf | 1 | 0 | 6 | 16.7% |

The Saboteur, the only member with security in scope, raised 7 findings in 10 runs.

## Decision

1. Rewrite the Saboteur's brief, the member that covers the gap. Its old brief lists no browser-side defences (frame headers, origin checks, `postMessage`), and its bar ("name the attacker who controls the input") lets it drop input that only an admin sets.
2. In the same plugin change, the two rule fixes from the pilot's remaining denied commands.
3. Re-run the 10 pilot boards on account B, re-judge, and apply Gate 2 again with the same rule. `/code-review` runs are kept.

These are development PRs, so tuning on them is allowed. To limit overfitting, the brief change has to be a general statement about security review, checked against how other security review prompts are written, not a list of `discourse-4`'s issues. The test split stays unseen. If the board still fails the gate after this change, the next step is a design rethink, not another tuning round.
