---
name: review-revise
description: Hands-off (man-free, unattended, automatic) review and revise of a PR or branch. 5 independent native /code-review runs, keep only the issues all 5 found, then fix or decline each one, with the fixes committed to a new local branch. Use when the user asks for a man-free, hands-off, unsupervised or automatic review and revise (or review and fix) of a PR, a branch or their changes.
argument-hint: "[PR number or URL | base..head] [--review-only]"
allowed-tools: Bash(python *) Bash(python3 *) Bash(py *) Bash(git show *) Bash(git log *) Read
---

# Hands-off review and revise

The user wants this done without supervising it, so **don't ask questions and don't stop for confirmation**. Run it, wait for it, and report.

## Run it

1. Pick the target from the user's words:
   - A PR number or URL ("PR 123", "#123", a github.com/.../pull/123 link): pass it.
   - A range ("main..HEAD"): pass it.
   - "This PR", "this branch", "my changes", or nothing: pass nothing. The script then uses the current branch, against its open PR's base (via `gh`), or else its upstream or `main`.
   - Add `--review-only` only if the user asks for a review without fixes.
2. Run the script from the repository **in the background**, since it takes 5–15 minutes:

   ```
   python "${CLAUDE_SKILL_DIR}/scripts/review_revise.py" <target>
   ```

   Use `python3` or `py` if `python` isn't found. It needs Python 3.10+, `claude` on PATH, and `gh` for PR numbers. Tell the user in one line that it's running, then wait for it to finish without polling.
3. When it finishes, give the user the summary it printed: the kept issues, what was fixed or declined and why, and the branch holding the fixes. If it failed, show the error and the one thing that would fix it.

## What it does and doesn't touch

- It works in a temporary git worktree at the change's head. The user's working tree, current branch and uncommitted changes are never touched, and a PR needn't be checked out.
- The fixes become one commit on a new local branch, `review-revise/<target>-<time>`. Nothing is pushed or merged. **Don't push, merge or comment on the PR yourself** unless the user asks.
- Remind the user, in one line, to read the fixes (`git show <branch>`) before taking them: on the benchmark's test PRs, about 1.4 edits per PR fixed nothing and about 1.3 planted bugs per PR were still left.
- Every session's output is kept under `.git/review-revise/<time>/`. Each run costs about $0.40 (API-equivalent) on the user's login.
