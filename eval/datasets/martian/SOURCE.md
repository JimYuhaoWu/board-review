# Martian Code Review Bench (offline set)

- **Source:** https://github.com/withmartian/code-review-benchmark
- **Pinned commit:** `e616e849755441da38f18bf3adba2c9583b03803` (2026-08-30)
- **License:** MIT
- **Contents used:** `offline/golden_comments/*.json` (50 PRs, 173 golden comments) and the pipeline in `offline/code_review_benchmark/` (extract, dedup, judge).
- **Known unusable PRs:** the golden file marks 5 PRs with an `az_comment`: 4 as "reviewed commit is not in the repo", 1 as "a mix of many PRs". Phase 2 confirms the runnable count.

The adapter that converts this set into the shared dataset format (`prs.jsonl`, `golden.jsonl`, `split.json`) is built in Phase 2.
