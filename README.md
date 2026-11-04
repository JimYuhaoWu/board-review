# board-review

A Claude Code skill for reviewing code after a long coding session. A board of specialist reviewers looks at the change. The user picks the members before each run. Separate verifiers label every finding as CONFIRMED, PLAUSIBLE, or REFUTED, based on evidence.

The repo also holds a reusable evaluation pipeline built on the [Martian Code Review Bench](https://github.com/withmartian/code-review-benchmark). It decides which members belong on the default board, and makes it cheap to test new members, including members for specific fields.

**Status:** Phase 1 (building the skill). The default roster is still a hypothesis until the evaluation selects it. See [docs/PLAN.md](docs/PLAN.md) and [docs/SETUP.md](docs/SETUP.md).

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
