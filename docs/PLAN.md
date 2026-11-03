# Board Review: design and evaluation plan

Status: **approved, not started**. Phase 0 (setup) is next.

## 1. Goal

Build a Claude Code skill, `/board-review`, that reviews code after a long coding session with minimal supervision.

The skill runs a **board** of specialist reviewers. The user chooses which members sit on the board before each run. An adjudicator then labels every finding as CONFIRMED, PLAUSIBLE, or REFUTED, based on evidence, not on a vote.

We need data to answer two questions:

1. Which members belong on the **default board**, and which stay **optional**?
2. Does the board beat Claude Code's built-in `/code-review`?

The evaluation pipeline must also stay reusable. Testing a new member, or a member for a specific field, must take minimal effort and minimal tokens.

## 2. Design principles

These come from the investigation that led to this plan.

- **Specialist members, no voting.** Members are the same model with different prompts, so their errors are correlated. A majority vote also discards findings that only one specialist can see, such as a security hole that only the saboteur notices.
- **Evidence decides labels.**
  - CONFIRMED: a failing test, command output, or a file:line trace from input to wrong output that a separate verifier re-traced.
  - PLAUSIBLE: the code path exists, but the trigger is not shown or depends on runtime state.
  - REFUTED: concrete counter-evidence, such as a guard at file:line, a passing test, or a type that rules out the input.
- **Two verifiers for High and Critical findings.** If they disagree, the label is PLAUSIBLE.
- **Members are independent.** No member sees another member's output. This keeps the reviews diverse, and it makes subset scoring and caching possible (section 6).
- **Personas are a focus area plus an attack list.** Character acting costs tokens and finds nothing.
- **Read-only by default.** Fixes are a separate, opt-in step. Unattended fixes run in a git worktree on their own branch.

## 3. Member pool

The "Golden issues" column shows how many of the 173 golden issues in the Martian offline benchmark fall in the matching category. The "Hypothesis" column is a guess; Phase 3 decides.

| id | Member | Focus | Golden issues | Hypothesis |
|---|---|---|---|---|
| `intent` | Intent auditor | Does the diff do what the PR or task claims? Is anything claimed done but missing? Do tests mock the thing they test? | spread across all | default |
| `edge` | Anti-reckless | Edge cases, empty/null/timeout input, partial failure, swallowed errors, silent fallbacks | bug 94, data 7 | default |
| `saboteur` | Saboteur | Trust boundaries, auth, injection, data exposure. Describes the attack path; never attacks a live system | security 11 | default |
| `intern` | Intern | Explain-back: writes in plain words what each changed function does. A mismatch with the stated intent becomes a finding; the explanation also goes into the report for the human | spread across all | default |
| `concurrency` | Concurrency checker | Races, ordering assumptions, stale state, re-entrancy | concurrency 14 | optional |
| `contract` | Contract checker | API, schema, and signature changes that break callers; version skew | api 13 | optional |
| `perf` | Algorithm expert | Complexity and query patterns. Runs only when the change touches loops over data, queries, or hot paths | perf 6 | optional |
| `nitpicker` | Nitpicker | Naming, structure, inheritance vs. duplication | style 10 | optional |

Domain members, such as a member for bioinformatics pipelines, can't be measured on Martian because Martian contains only web applications. They are tested on domain datasets (section 6.3).

## 4. Skill layout

```
skill/board-review/
  SKILL.md          orchestrator: scope → roster → dispatch → dedupe → adjudicate → report
  members/<id>.md   one file per member (section 6.1)
  adjudicator.md    evidence rules for CONFIRMED / PLAUSIBLE / REFUTED
  roster.json       named rosters: "default", "full"; Phase 3 fills in "default"
  schema.json       finding = {member, file, line, claim, evidence, severity, category}
```

How the user picks members:

- `/board-review --members default|full|intent,edge,...` picks them on the command line.
- With no argument in an interactive session, the skill shows a multi-select list with the default members pre-ticked.
- Benchmark runs always pass the argument.

## 5. Evaluation plan

### Phase 0: setup

- Claude Code 2.1.288 or later, which `--max-findings` needs.
- A judge API key for an OpenAI-compatible endpoint, such as the Martian gateway or OpenRouter. Each person keeps the key in their own local `.env`; it is never committed.
- Allow Claude Code to run the benchmark's Python pipeline (MIT-licensed). Auto mode may block external code until a permission rule allows it.
- Agree on a budget ceiling. The pilot measures the real cost per run.
- On Windows, set `core.autocrlf=false` in every benchmark clone. Otherwise CRLF conversion makes whole files appear changed in the diff.

### Phase 1: build the skill

Write the orchestrator, the eight members, the adjudicator, and the schema from sections 3 and 4.

### Phase 2: build the harness

1. **Fetch.** Clone each benchmark PR's fork and record the base and head commits. The Martian golden file flags 5 of the 50 PRs as unusable: 4 because "reviewed commit is not in the repo", 1 because it mixes many PRs. Confirm the real runnable count; PR-AF reports 38.
2. **Run.** Each (variant, PR, repeat) gets a fresh worktree at the head commit and one `claude -p ... --output-format json` run.
   - Variant a: `/code-review high --max-findings all <base>..<head>`
   - Variants b and c: `/board-review --members <roster> <base>..<head>`
   - All variants in one comparison use the same model and effort. The level is typed explicitly, so `/code-review` can't reuse a remembered level.
3. **Prevent leakage.**
   - Edit, Write, WebFetch, and WebSearch are disallowed, so a reviewer can't look up the upstream fix.
   - The working directory is outside the benchmark repo, so the reviewer never sees the golden comments.
4. **Score every variant the same way.** Each review is rendered as one general comment and goes through Martian's own extraction, dedup, and judge steps, unmodified. The board's structured JSON gets no advantage over `/code-review`'s text.
5. **Score board runs three ways:** CONFIRMED only, CONFIRMED + PLAUSIBLE, and the raw union before adjudication.
6. **Log for every run:** cost (`total_cost_usd`), wall time, tokens, which conditional members fired, and the run metadata in section 6.4.

### Phase 3: pilot, then select the default board

**Pilot:** 10 PRs (2 per repo), 1 repeat, variants a and c. Two gates:

- **Gate 1, judge sanity:** re-judging the stored `claude-code` row in the Martian results must come close to its published score.
- **Gate 2, cost:** the measured cost per run decides the number of repeats.

**Freeze the split** before looking at any result: about 20 PRs for development and about 25 for testing, stratified by repo.

**On the development split:** run the full board 3 times. For each member, report:

- Unique true positives: golden issues only that member found.
- False positives that member adds.
- Precision, and the member's share of the cost.

Then score every subset of members offline from those runs (section 6.2).

**Selection rule**, fixed now so the data can't bend it:

1. Find the subset with the best F1.
2. The default board is the **smallest** subset within one bootstrap standard error of that best F1, with precision at least that of `/code-review`.
3. Members outside it become optional.
4. A member with zero unique true positives across the development split leaves the pool.

**Adjudicator checks on the development split:**

- **Wrongful refutation rate:** how often a REFUTED finding matches a real golden issue. This is the most important number for unattended use, because each case is a real bug the board discarded.
- CONFIRMED precision must be clearly higher than PLAUSIBLE precision. If it isn't, the labels carry no information.

Both collaborators review this result before Phase 4.

### Phase 4: final comparison

- **Variants:** a (`/code-review`) vs. b (default board) vs. c (full board), on the test split, 3 repeats each.
- **Judges:** a panel of 3 (Claude Opus 4.5, Claude Sonnet 4.5, GPT-5.2), where 2 of 3 must agree. Including GPT-5.2 limits Claude judging Claude.
- **Metrics:**
  - Precision, recall, F1, and F2 on Martian's Core and Strict profiles.
  - Recall per category. For example, does the saboteur raise security recall?
  - Cost and wall time per PR.
- **Statistics:** paired bootstrap over PRs.

About 25 test PRs means only large differences will be significant. A secondary result on all runnable PRs is reported too, flagged as optimistic for b, because b's members were selected on part of that data.

### Budget

| Phase | Runs |
|---|---|
| Pilot | ~20 |
| Development split (full board × 3) | ~60 |
| Test split (3 variants × 3) | ~225 |
| **Total** | **~305**, plus judge calls |

Runs through `claude -p` count against the runner's plan usage and rate limits. Dropping to 2 repeats cuts the total by about a third.

## 6. A reusable evaluation pipeline

The pipeline is built once. After that, testing a new member, or a member for a specific field, should take one command and only that member's tokens.

### 6.1 A member is one file

```markdown
---
id: perf
name: Algorithm expert
categories: [perf]            # golden categories this member targets
trigger: "diff touches loops over data, DB queries, or hot paths"   # omit = always runs
status: candidate             # candidate | optional | default | retired
---
Focus, attack list, and the output contract (schema.json).
```

The orchestrator and the harness discover members from `members/*.md`. Adding a member needs no code change.

### 6.2 Cache findings per member, score subsets offline

Members are independent, so their findings can be cached separately.

- **Findings cache key:** dataset, PR, repeat, member id, hash of the member file, reviewer model, effort, and CLI version.
- **Adjudication cache key:** finding hash, hash of the adjudicator file, and model.

Consequences:

- **Testing a new member** runs only that member. Every subset that includes it is then scored by combining its findings with the cached findings of the others.
- **Editing one member** re-runs only that member.
- **Editing the adjudicator** re-runs adjudication only, not the members.

**Subset scoring:** dedup groups are computed once on the full union of findings.

- A golden issue counts as found when any member in the subset matched it.
- Unmatched findings in the subset count as false positives, with duplicates collapsed through the union's groups.

### 6.3 Datasets share one format

```
eval/datasets/<name>/
  prs.jsonl       {id, repo, base, head, intent}
  golden.jsonl    {pr_id, comment, severity, category}
  SOURCE.md       where the data comes from, license, pinned commit
```

- `martian`: an adapter that converts the Martian offline set into this format, pinned to one commit.
- **Domain datasets**, built with the method Qodo and Macroscope use:
  1. Find commits that fixed a bug.
  2. Trace each bug back to the commit that introduced it, and use that commit as the review task.
  3. Add control commits with no known bug, so precision can be measured.

  20–30 cases is enough to start.
- Categories extend Martian's taxonomy. A domain dataset may add its own categories, such as `coordinates` or `genome-build`.

### 6.4 Every run is reproducible

Each run records: dataset commit, PR id, variant, roster, member file hashes, adjudicator hash, reviewer model, effort, Claude Code version, judge models and judge prompt version, cost, time, and who ran it.

Results from different reviewer models or judges are never pooled.

### 6.5 Member cards

`eval report --member <id>` writes a card for one member, per dataset:

- Unique true positives and false positives added.
- Precision and recall per category.
- Cost share.
- Its marginal effect on the default board.

Promotion from candidate to optional to default uses the same rule as Phase 3, on the development split only.

### 6.6 Protect the test split

The test split is used only for release comparisons, and each use is logged. Repeated peeking turns it into a second development split and inflates the scores.

### 6.7 Testing a new member, end to end

1. Add `members/<id>.md` with `status: candidate`.
2. Run `eval run --dataset <name> --split dev --members <id>`. Only the new member runs; everything else comes from the cache.
3. Run `eval report --member <id>`.
4. If the card meets the promotion rule, change `status`, and update `roster.json` when it joins the default board.

## 7. Repository layout (target)

```
board-review/
  docs/PLAN.md                 this file
  skill/board-review/          the skill (section 4)
  eval/
    datasets/<name>/           shared dataset format (section 6.3)
    runner/                    worktrees, claude -p runs, caching
    scoring/                   wraps Martian's extract/dedup/judge steps; subset scoring
    reports/                   member cards and comparison reports (committed)
    results/                   raw run output (git-ignored; shared as release assets when needed)
```

## 8. Working together

- Work on branches and merge through pull requests.
- Commit summaries and reports; keep raw run output out of git.
- Never commit API keys or `.env` files.
- The token-heavy work, the member runs, can be split between both accounts by dataset or by member. Within one comparison, all variants must use the same reviewer model.

## 9. Open decisions

1. **Reviewer model for the comparison:** Claude Opus 5.5 or Claude Fable 5.1. Results from the two are kept separate.
2. **Judge endpoint** and who pays for judge calls.
3. **Budget ceiling** per phase.
4. **Who runs which phase.**

## 10. Known limits

- **Training leakage:** Martian PRs come from well-known public repositories, so the models may have seen them.
- **No test execution:** the benchmark repos aren't built locally, so CONFIRMED rests on code traces, not failing tests.
- **Web applications only:** results on Martian don't transfer automatically to other fields. That is why domain datasets exist.
- **Variable intent text:** the intent auditor depends on PR descriptions, and their quality varies.

## References

- Martian Code Review Bench: https://github.com/withmartian/code-review-benchmark
- ironbee raw-LLM harness on Martian: https://github.com/ironbee-ai/code-review-benchmarks
- PR-AF: https://github.com/Agent-Field/pr-af
- Claude Code `/code-review`: https://code.claude.com/docs/en/code-review-local
- Ultrareview: https://code.claude.com/docs/en/ultrareview
- Debate or Vote (NeurIPS 2025): https://neurips.cc/virtual/2025/poster/116557
- OpenAI Codex plugin for Claude Code (adversarial review prompt): https://github.com/openai/codex-plugin-cc
