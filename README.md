# board-review

A Claude Code skill for reviewing code after a long coding session. A board of specialist reviewers looks at the change. The user picks the members before each run. Separate verifiers label every finding as CONFIRMED, PLAUSIBLE, or REFUTED, based on evidence.

The repo also holds a reusable evaluation pipeline built on the [Martian Code Review Bench](https://github.com/withmartian/code-review-benchmark). It decides which members belong on the default board, and makes it cheap to test new members, including members for specific fields.

**Status:** closed 2026-11-23. The evaluation did not select a board over native `/code-review`; the findings, and what review followed by automated revision still leaves to a human, are in [eval/reports/final.md](eval/reports/final.md). Plans: [PLAN.md](docs/PLAN.md), [PLAN-v2.md](docs/PLAN-v2.md), [PLAN-v3.md](docs/PLAN-v3.md).

## Review and revise (recommended)

The evaluation's result, packaged as the skill `review-revise`: 5 independent native `/code-review medium` runs, keep only the issues all 5 found, then a reviser fixes each kept issue or declines it with a reason. It runs unattended in a temporary git worktree and commits the fixes to a new local branch, `review-revise/<target>-<time>`. Your working tree is never touched, and nothing is pushed.

Install it once as a personal skill (Windows; on macOS or Linux, `ln -s`):

```powershell
New-Item -ItemType Junction -Path "$env:USERPROFILE\.claude\skills\review-revise" -Target "<clone>\plugin\skills\review-revise"
```

Then just ask, in any Claude Code session in the repository:

```
I need a man-free review and revise on this PR
Hands-off review and fix of PR 123
/review-revise main..HEAD
```

It takes 5–15 minutes and about $0.40 (API-equivalent) on your login, and needs Python 3.10+ (and `gh` for PR numbers). Read the branch (`git show review-revise/...`) before taking it: on the benchmark's test PRs a human was still left with about 1.4 wrong edits and 1.3 missed bugs per PR ([eval/reports/r2.md](eval/reports/r2.md)).

## Try it

Load the plugin from a clone of this repo and start Claude Code in the repository you want reviewed:

```bash
claude --plugin-dir /path/to/board-review/plugin
```

Then, inside Claude Code:

```
/board-review:review                        # current branch vs upstream, uncommitted changes included; asks which members to use
/board-review:review --members full main...HEAD
/board-review:review --members intent,edge,saboteur 1234        # a pull request
/board-review:review --format json HEAD~3..HEAD                  # machine-readable output
```

The review is read-only. Ask Claude to fix the CONFIRMED findings afterwards if you want them fixed.

## Members

| id | Focus | Default |
|---|---|---|
| `intent` | Does the change do what it claims? | yes |
| `edge` | Edge cases, failure paths, swallowed errors | yes |
| `saboteur` | Security: injection, auth, data exposure | yes |
| `intern` | Explains what the change does; flags mismatches | yes |
| `concurrency` | Races, ordering, stale state (runs when relevant) | no |
| `contract` | Interface, schema, and signature breakage (runs when relevant) | no |
| `perf` | Complexity and query patterns (runs when relevant) | no |
| `nitpicker` | Text that is wrong: names, docs, comments, messages | no |
| `line-reader` | Every changed line: won't parse or run, wrong conditions, variables, indices | no |
| `regression` | Old behavior the change drops: defaults, migrations, removed steps (runs when relevant) | no |
| `boundary` | Values crossing serialization, storage, processes, and external APIs | no |
| `test-auditor` | Tests that can't catch what they claim to (runs when relevant) | no |

A member is one Markdown file in `plugin/skills/review/members/`. See PLAN section 6 for adding one.
