---
name: review-revise
description: Review a branch with 5 independent native /code-review runs, keep only the issues all 5 found, then fix each kept issue (or decline it with a reason) as uncommitted edits. Use when the user wants a low-noise review followed by automatic fixes, or asks for "review and revise".
argument-hint: "[--review-only] [--intent text] [base..HEAD]"
allowed-tools: Bash(python *) Bash(python3 *) Bash(py *) Bash(git diff *) Bash(git status *) Read
---

# Review, then revise

This skill runs the pipeline measured in the board-review project (`eval/reports/final.md`):

1. **Review:** `/code-review medium` in 5 independent `claude -p` sessions (read-only).
2. **Match:** one call groups the findings that report the same defect. Only the issues **all 5** reviews found are kept, which cut false positives by about two thirds.
3. **Revise:** one session fixes each kept issue with the smallest change, or declines it with a reason. The edits are left uncommitted.

## Run it

1. Check that the working tree has no uncommitted changes to tracked files (`git status`), unless the user passed `--review-only`. If it does, ask the user to commit or stash them first. Don't do it yourself.
2. Run the script from the repository, passing the user's arguments through unchanged:

   ```
   python "${CLAUDE_SKILL_DIR}/scripts/review_revise.py" $ARGUMENTS
   ```

   Use `python3` or `py` if `python` isn't found. The script needs Python 3.10+ and `claude` on PATH. It takes about 5–15 minutes, so run it with a timeout of at least 30 minutes, or in the background. Each run costs about $0.40 (API-equivalent) on the user's login.
3. Show the user the summary the script prints: the kept issues, what was fixed or declined and why, and the `git diff --stat`.
4. Tell the user to read `git diff` before committing. In the benchmark, even with this pipeline, about 1.4 edits per PR fixed nothing and about 1.3 planted bugs per PR were missed. Don't commit or revert anything yourself.

## Options

- `base..HEAD`: the change to review. By default it is the branch since it left its upstream (or `main`/`master`). The range must end at `HEAD`.
- `--review-only`: stop after the review, with no edits.
- `--intent "..."`: what the change is meant to do. By default the range's commit messages are used.
- `--runs`, `--model`: change the measured setting (5 runs, `claude-sonnet-5-5`). The results were measured only for the defaults.

Every session's output is kept under `.git/review-revise/<time>/`: the reviews, the matching, the findings file and the reviser's report.
