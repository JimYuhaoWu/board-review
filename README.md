# board-review

A Claude Code skill for reviewing code after a long coding session. A board of specialist reviewers looks at the change. The user picks the members before each run. An adjudicator labels every finding as CONFIRMED, PLAUSIBLE, or REFUTED, based on evidence.

The repo also holds a reusable evaluation pipeline built on the [Martian Code Review Bench](https://github.com/withmartian/code-review-benchmark). It decides which members belong on the default board, and makes it cheap to test new members, including members for specific fields.

**Status:** plan approved; setup (Phase 0) is next. Read [docs/PLAN.md](docs/PLAN.md) for the full plan, including the decisions in section 10.
