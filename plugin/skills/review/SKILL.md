---
name: review
description: Board review of a code change. Independent specialist reviewers (intent, edge cases, security, explain-back, plus optional concurrency, contracts, performance, wrong text, line-by-line reading, regressions, boundaries, and tests) each read the change, then separate verifiers label every finding CONFIRMED, PLAUSIBLE, or REFUTED from evidence. Use after a long coding session or before merging, when the user asks for a board review.
argument-hint: "[--members default|full|id,id] [--format markdown|json] [--intent text | --intent-file path] [--extra-findings path] [target]"
allowed-tools: Read Grep Glob Bash(git diff *) Bash(git show *) Bash(git log *) Bash(git rev-parse *) Bash(git merge-base *) Bash(git status *) Bash(git grep *) Bash(git blame *) Bash(git ls-files *) Bash(gh pr view *) Bash(gh pr diff *)
---

# Board review

Run a review board over one code change. You are the **orchestrator**. You do not review the code yourself. You resolve the scope, dispatch the members, group their findings, dispatch the verifiers, and write the report.

Hard rules for the whole run:

- **Read-only.** Never edit, create, or delete files. Never commit, push, or post comments.
- **No network** except `gh pr view` and `gh pr diff` when the target is a pull request.
- **One command per Bash call.** Read files with the Read tool and search with Grep, not `cat`, `head`, `grep`, or `python -c`, and run one git command per Bash call, without `cd`, `git -C`, or `&&`. Only single read-only commands are pre-approved, so a chained command is denied and costs a turn.
- **Independence.** Members and verifiers get only what this file says to give them. Never pass them this conversation's history, your own opinion of the code, or another member's findings.
- **No fixes.** This skill reports. Fixing is a separate request from the user.

Arguments: `$ARGUMENTS`

## 1. Parse the arguments

| Flag | Values | Default |
|---|---|---|
| `--members` | `default`, `full`, or comma-separated member ids | ask the user (step 4); `default` when you can't ask |
| `--format` | `markdown` or `json` | `markdown` |
| `--intent` | text up to the next flag | none |
| `--intent-file` | path to a text file | none |
| `--max-findings` | findings per member | `8` |
| `--extra-findings` | path to a JSON file with one `member_output` object from a reviewer that ran outside this board | none |

Everything else is the **target**. Unknown flags are an error: stop and say which flag is unknown.

## 2. Resolve the scope

Work out a base commit and a head, and the exact diff command, from the target:

| Target | Base | Head | Diff command |
|---|---|---|---|
| none | merge-base with the upstream branch, else with `main`/`master`, else `HEAD~1` | working tree | `git diff <base>` (includes uncommitted changes) |
| `A..B` | `A` | `B` | `git diff A..B` |
| `A...B` | `git merge-base A B` | `B` | `git diff A...B` |
| one commit `abc123` | `abc123^` | `abc123` | `git diff abc123^..abc123` |
| PR number or URL | the PR's base | the PR's head | `gh pr diff <n>` |
| a path | as for "none" | working tree | `git diff <base> -- <path>` |

Resolve every ref to a full SHA with `git rev-parse`. Then collect:

- The changed files and line counts (`--stat`).
- **Excluded files:** lockfiles, generated code, vendored dependencies, and minified assets. List them; members skip them.
- **Untracked files** when the head is the working tree (`git status --porcelain`). Treat them as added files.

If the diff is empty, stop and say so. If it is over about 3,000 changed lines, say that coverage will be thinner, and continue.

## 3. Write the intent

Members judge the change against what it was meant to do. Write the **stated intent** in 2–6 plain sentences, from the first source that exists:

1. `--intent` or `--intent-file`.
2. The pull request's title and description.
3. The commit messages in the range.
4. In an interactive session: what the user asked for in this conversation. Summarize the request, not your own work.

Write what the change claims to do. Don't judge it, and don't mention known problems.

## 4. Choose the roster

Read `${CLAUDE_SKILL_DIR}/roster.json` and the member briefs in `${CLAUDE_SKILL_DIR}/members/`. Each brief's frontmatter has `id`, `name`, `trigger`, and `status`.

- `--members default` or `full`: use that roster from `roster.json`.
- `--members a,b,c`: use exactly those ids. An unknown id is an error: stop and list the valid ids.
- No `--members`, and you can ask the user: use AskUserQuestion with `multiSelect: true`, two questions:
  1. "Core members": the members in the `default` roster, labels marked "(Recommended)".
  2. "Optional members": the other members.

  Each option's description is the member's one-line focus.
- No `--members`, and you can't ask: use `default`.

Then apply **triggers**. A member with a `trigger` runs only if the diff matches it. Decide from the changed files and the diff, and record a one-line reason for every member you skip.

If `--extra-findings` is given, read the file with Read. It must hold one JSON object matching `member_output`, whose `member` is the reviewer's id, such as `code-review`. This is an **extra member**: it already ran, so don't dispatch it, and don't apply a trigger to it. Its id must not be one of the roster's ids. If the file is missing or isn't valid, record the extra member under `members.failed` with the reason, and continue without it.

## 5. Dispatch the members

In **one message**, start one Agent per running member, all in parallel, with `subagent_type: "board-review:board-member"` and `run_in_background: false`, so every result comes back in this turn. Don't poll for agents or wait on notifications. Give each member exactly this prompt, filled in:

```
Member brief: read ${CLAUDE_SKILL_DIR}/members/<id>.md
Output schema: ${CLAUDE_SKILL_DIR}/schema.json, definition "member_output"
Repository root: <absolute path>
Base: <sha>   Head: <sha or "working tree">
Diff command: <exact command from step 2>
Changed files: <list with line counts>
Excluded files: <list or "none">
Stated intent:
<intent from step 3>
Finding budget: at most <max-findings> findings.
```

Nothing else goes in the prompt.

## 6. Collect and group

Each member returns one JSON object (`member_output`). If a member's output isn't valid JSON, or the member failed, record it under `members.failed` with the reason, and continue without it.

Add the extra member's findings, if any, as that member's output, and list its id in `members.ran`. From here on it is a member like any other: its findings are grouped and verified the same way.

Give every finding an id `<member>-<n>`. Then group findings that describe the **same problem**, even if worded differently. Each group gets an id `G1`, `G2`, and so on. Keep every finding; grouping never deletes one. A group's severity is the highest among its findings.

Two findings are the same problem only when one fix resolves both and they describe the same wrong outcome. Being near each other, in the same function, or about the same input isn't enough. When in doubt, keep them apart: a separate group costs one more verifier claim, while a wrong merge hides a problem from the report. A finding that names a problem no other finding in its group names gets its own group. The report shows the first finding of each group, so put first the finding whose claim states the group's problem most completely.

## 7. Adjudicate

**Pass 1.** Split the groups into batches of at most 6. In one message, start one verifier per batch, in parallel, with `subagent_type: "board-review:board-verifier"` and `run_in_background: false`:

```
Output schema: ${CLAUDE_SKILL_DIR}/schema.json, definition "verifier_output"
Repository root: <absolute path>
Base: <sha>   Head: <sha or "working tree">
Diff command: <exact command>
Stated intent:
<intent>
Claims to verify:
<for each group: group id, then every finding in it as JSON: file, line, end_line, claim, evidence, severity, category>
```

**Pass 2.** For every group whose severity is High or Critical, start a **second, independent** verifier with the same prompt format. Never show it the pass-1 verdicts.

**Combine** the verdicts per group:

| Pass 1 | Pass 2 | Label |
|---|---|---|
| any | not run | pass 1's label |
| CONFIRMED | CONFIRMED | CONFIRMED |
| REFUTED | REFUTED | REFUTED |
| any other combination | | PLAUSIBLE |

A verifier that fails or returns invalid output counts as PLAUSIBLE for its groups, with the reason "verifier failed". Every finding in a group gets the group's label.

## 8. Report

### `--format json`

Your final message is **one JSON object** matching `board_output` in `${CLAUDE_SKILL_DIR}/schema.json`, with no text before or after it and no code fence. Include every finding with its label, including REFUTED ones. Set `members.roster` to `default`, `full`, or `custom`; an extra member doesn't change it.

Keep the JSON compact: no indentation, and copy each field as the member or verifier wrote it. Keep the whole object under about 30,000 characters. If it would be longer, shorten `evidence` fields to their first sentence, starting with REFUTED findings. Never drop a finding.

### `--format markdown` (default)

```
# Board review: <short target description>

<base>..<head>, <n> files, <+added/-removed> lines
Members: <ran>   Skipped: <id (reason)>   Failed: <id (reason)>

## Confirmed (<n>)
<per group, highest severity first:>
- **[Severity] path/file.ext:line**: <claim>
  Evidence: <one or two sentences from the verifier>
  Found by: <member names>

## Plausible (<n>)
<same format; say what would settle it>

## Refuted (<n>)
- path:line: <claim> (refuted: <reason>)

## What the change does
<the intern's explain-back, if the intern ran>
```

Leave out an empty section. After the report, in an interactive session, add one line: the user can ask you to fix the CONFIRMED findings. Don't fix anything unless they ask.

## Reference

- Member briefs: `${CLAUDE_SKILL_DIR}/members/*.md`
- Rosters: `${CLAUDE_SKILL_DIR}/roster.json`
- Output schemas: `${CLAUDE_SKILL_DIR}/schema.json`
- Member and verifier contracts: the `board-member` and `board-verifier` agents in this plugin
