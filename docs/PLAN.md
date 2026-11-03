# Board Review: design and evaluation plan

Status: **approved, not started**. Phase 0 (setup) is next.

## 1. Goal

Build a Claude Code skill, `/board-review`, that reviews code after a long coding session with minimal supervision.

The skill runs a **board** of specialist reviewers. The user chooses which members sit on the board before each run. An adjudicator then labels every finding as CONFIRMED, PLAUSIBLE, or REFUTED, based on evidence, not on a vote.

We need data to answer two questions:

1. Which members belong on the **default board**, and which stay **optional**?
2. Does the board beat Claude Code's built-in `/code-review`?

The evaluation pipeline must also stay reusable. Testing a new member, or a member for a specific field, must take minimal effort and minimal usage.

The whole experiment must fit inside two subscription plans (section 7).

## 2. Design principles

- **Specialist members, no voting.** Members are the same model with different prompts, so their errors are correlated. A majority vote also discards findings that only one specialist can see, such as a security hole that only the saboteur notices.
- **Evidence decides labels.**
  - CONFIRMED: a failing test, command output, or a file:line trace from input to wrong output that a separate verifier re-traced.
  - PLAUSIBLE: the code path exists, but the trigger is not shown or depends on runtime state.
  - REFUTED: concrete counter-evidence, such as a guard at file:line, a passing test, or a type that rules out the input.
- **Two verifiers for High and Critical findings.** If they disagree, the label is PLAUSIBLE.
- **Members are independent.** No member sees another member's output. This keeps the reviews diverse, and it makes caching and offline subset scoring possible (section 6). Any roster's result can be computed from a full-board run without running that roster again.
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

### 5.1 Fixed settings

- **Benchmark:** Martian Code Review Bench, offline set, pinned to one commit.
- **Reviewer model: Claude Sonnet 5.5 at `medium` effort, for every variant.** Sonnet 5.5 runs on both accounts within plan limits, while Fable runs on usage credits on both (section 7). In the ironbee raw-model results, Sonnet 5.5 tied for the top group at about half of Opus 5.5's cost. Higher effort did not raise Claude review scores there.
- **Variants:**
  - **a:** built-in `/code-review medium --max-findings all <base>..<head>`.
  - **c:** full board, `/board-review --members full <base>..<head>`.
  - **b:** default board. **Not run separately.** It is computed offline from variant c's findings, because members are independent.
- **Judge:** Martian's extraction, dedup, and judge steps, unchanged. Judge models are called through an OpenAI-compatible endpoint, outside both plans.
  - **Running the judges inside the plans was investigated and rejected (Yara, 2026-11-03).** Martian's judge makes one small call per pair of golden comment and candidate, about 14 pairs per review in its stored results. This project needs about 9,500–26,000 judge calls, roughly $50–100 on a paid API, mostly for the final panel. Routing those calls through `claude -p` can't set temperature 0, can't run GPT-5.2, and uses a lot of plan usage. Batching many pairs into one call would change Martian's method and need its own validation.
  - **Judge choice moves scores.** Martian's three judges agree on 95% of golden comments (Cohen's κ ≈ 0.90), yet Claude Code's published F1 ranges from 39% (Sonnet 4.5) to 49% (Opus 4.5). Scores are only ever compared under the same judge.
  - **Free local endpoint.** Account A has free access to an institutional OpenAI-compatible endpoint, reachable from the institution's network. It serves DeepSeek V4.1 Flash (`deepseek-flash`), GLM 5.3 Flash (`glm-flash`), and Qwen 3.8 Flash-Next (`Qwen`). The address and key stay in account A's local `.env`. The key is personal, so all judging runs on account A's machine.
  - **Development judge:** `deepseek-flash` on the local endpoint, if it passes Gate 1b. A non-Claude judge also avoids Claude grading Claude.
  - **Final panel:** 3 judges from different model families; 2 of 3 must agree.
    - **Chosen, free:** the 3 local models (DeepSeek, GLM, Qwen). Valid for comparing a, b, and c. The scores can't be compared with the published leaderboard.
    - **Backup, paid:** Claude Opus 4.5, Claude Sonnet 4.5, and GPT-5.2, the judges of the published results, through a multi-model provider such as OpenRouter. About $50–100. Used if a local judge fails Gate 1b and no other local model can replace it, if the local endpoint becomes unavailable, or if leaderboard-comparable scores are needed.
  - **Rules for the local endpoint.**
    - Log the served model version with every judge call. The models change without notice, so judge each comparison within one short window, and re-judge everything if the version changes.
    - No quotas are published, and the endpoint is shared. Keep concurrency low and back off on errors.
    - Key test (2026-11-03): all 3 models answered a judge-style prompt with valid JSON, with `temperature: 0` accepted, in 0.7–2.5 s. The response reports only the alias (`deepseek-flash`, `glm-flash`, `Qwen`), not the version, so the harness records the version from the provider's model list at each judging session. Whether temperature 0 gives repeatable verdicts is checked in Gate 1b by judging the same pairs twice.

### Phase 0: setup

- **One pinned Claude Code version on both machines: 2.1.317.** It must be 2.1.288 or later, which `--max-findings` needs. Install it as a standalone CLI with `claude install <version>`, log it in to the account's plan, and set `DISABLE_AUTOUPDATER=1`. Don't run benchmarks with the copy bundled in the desktop app: it updates with the app and isn't logged in when run on its own.
- **A clean Claude Code setup for benchmark runs**, the same on both machines. A personal setup (user CLAUDE.md, auto-memory, hooks, MCP servers, plugins, skills) changes what the reviewer writes and adds usage to every run. Tested on 2.1.309:
  - **Use:** a dedicated, empty config directory (`CLAUDE_CONFIG_DIR`, logged in once), plus `--plugin-dir` to load the board-review skill from this repo. The user's skills are gone and the built-in `/code-review` stays. The skill must be packaged as a plugin, and it then shows up as `<plugin>:board-review`.
  - `--safe-mode` doesn't work: it hides skills loaded through `--plugin-dir`.
  - `--bare` doesn't work: it accepts only an API key, not a plan login.
- A judge API key for an OpenAI-compatible endpoint, such as the Martian gateway or OpenRouter. It stays in a local `.env` and is never committed.
- Allow Claude Code to run the benchmark's Python pipeline (MIT-licensed). Auto mode may block external code until a permission rule allows it.
- On Windows, set `core.autocrlf=false` in every benchmark clone. Otherwise CRLF conversion makes whole files appear changed in the diff.

### Phase 1: build the skill

Write the orchestrator, the eight members, the adjudicator, and the schema from sections 3 and 4.

Develop against 1–2 small benchmark PRs only. Full runs start in Phase 3.

### Phase 2: build the harness

1. **Fetch.** Clone each benchmark PR's fork and record the base and head commits. The Martian golden file flags 5 of the 50 PRs as unusable: 4 because "reviewed commit is not in the repo", 1 because it mixes many PRs. Confirm the real runnable count; PR-AF reports 38. This plan assumes about 40.
2. **Run.** Each (variant, PR, repeat) gets a fresh worktree at the head commit and one `claude -p ... --output-format stream-json --verbose` run, with the settings in 5.1. The model and effort are passed with `--model` and `--effort`, and `/code-review`'s level is typed in the command, so nothing falls back to a default or a remembered setting.
   - The runner reads the setup from the run's `init` event: Claude Code version, model, MCP servers, plugins, and skills. A run whose setup differs from the expected one is discarded.
3. **Prevent leakage.**
   - Edit, Write, WebFetch, and WebSearch are disallowed, so a reviewer can't look up the upstream fix.
   - The working directory is outside the benchmark repo, so the reviewer never sees the golden comments.
4. **Survive usage limits.**
   - A run that stops at a usage limit is marked `failed: usage-limit`, never recorded as an empty review.
   - The runner stops for that account and resumes after the reset.
   - Findings are cached per member, so a resumed run only re-runs the members that didn't finish.
5. **Score every variant the same way.** Each review is rendered as one general comment and goes through Martian's own extraction, dedup, and judge steps, unmodified. The board's structured JSON gets no advantage over `/code-review`'s text.
6. **Score board runs three ways:** CONFIRMED only, CONFIRMED + PLAUSIBLE, and the raw union before adjudication.
7. **Log for every run:** cost (`total_cost_usd`), wall time, tokens, which conditional members fired, the plan usage percentage before and after (section 7.2; only approximate if the account is used for other work at the same time), and the run metadata in section 6.4.

### Phase 3: measure, pilot, decide whether to continue

**Step 1, freeze the split.** Before any results are scored, split the runnable PRs into about 15 development PRs and about 25 test PRs, stratified by repo. The split is recorded in the repo and never changed.

**Step 2, measure usage (2 runs).** On the largest pilot PR, run variant a once and variant c once, while nothing else uses the account. `/usage` counts all work on the account, so other work would inflate the numbers. Record session and weekly usage before and after each run (section 7.2). This decides the number of repeats and the account split. These 2 runs also count as that PR's pilot runs.

**Step 3, pilot.** 10 development PRs (2 per repo), including the PR from step 2, variants a and c, 1 repeat. The pilot runs are kept as repeat 1 for those PRs. Two gates:

- **Gate 1, judge sanity:** re-judge the stored `claude-code` row with one of the published judges, and compare the result with that judge's published score. This checks our copy of the judge pipeline. It needs about 700 judge calls (50 reviews × ~14 pairs), a few dollars on a paid API.
- **Gate 1b, local-judge agreement** (free; judge calls only, no review runs): each local judge re-judges the stored rows of several published tools. Its ranking of those tools must agree closely with the published rankings. A local judge that fails isn't used.
- **Gate 2, early stop:** the full board must score a higher F1 than `/code-review` on the pilot PRs, using the development judge, Martian's Core profile, and board findings labeled CONFIRMED or PLAUSIBLE. If it doesn't, stop and rethink the design before spending more usage.

### Phase 4: full runs, select the default board

**Run variants a and c on all runnable PRs**, 2 repeats each, with the pilot runs counted. Development and test PRs use the same run set. Choosing the default board only picks a subset of findings that already exist, so test PRs need no separate runs. The split stays valid as long as nobody looks at test results before the choice is final.

**On the development PRs only**, for each member, report:

- Unique true positives: golden issues only that member found.
- False positives that member adds.
- Precision, and the member's share of the usage.

Then score every subset of members offline (section 6.2).

**Selection rule**, fixed now so the data can't bend it:

1. Find the subset with the best F1.
2. The default board is the **smallest** subset within one bootstrap standard error of that best F1, with precision at least that of `/code-review`.
3. Members outside it become optional.
4. A member with zero unique true positives across the development PRs leaves the pool.

**Adjudicator checks on the development PRs:**

- **Wrongful refutation rate:** how often a REFUTED finding matches a real golden issue. This is the most important number for unattended use, because each case is a real bug the board discarded.
- CONFIRMED precision must be clearly higher than PLAUSIBLE precision. If it isn't, the labels carry no information.

Both collaborators sign off on the selected default board before anyone scores the test PRs.

### Phase 5: final comparison

- **Variants:** a (`/code-review`) vs. b (default board, computed offline) vs. c (full board), on the test PRs.
- **Judges:** the 3-judge panel from 5.1.
- **Metrics:**
  - Precision, recall, F1, and F2 on Martian's Core and Strict profiles.
  - Recall per category. For example, does the saboteur raise security recall?
  - Usage and wall time per PR.
- **Statistics:** paired bootstrap over PRs.
- **If a and c are too close to call**, add a third repeat on the test PRs only.

About 25 test PRs means only large differences will be significant. A secondary result on all runnable PRs is reported too, flagged as optimistic for b, because b's members were selected on part of that data.

### 5.2 Run budget (about 40 runnable PRs)

| Step | Runs |
|---|---|
| Phase 3, usage measurement (counted as one pilot PR) | 2 |
| Phase 3, pilot on the other 9 PRs × 2 variants (kept as repeat 1) | 18 |
| Phase 4, repeat 1 on the remaining 30 PRs × 2 variants | 60 |
| Phase 4, repeat 2 on all 40 PRs × 2 variants | 80 |
| **Total** | **~160**, plus judge calls |
| Minimum option: 1 repeat only | ~80 |
| Optional: third repeat on test PRs | +50 |

The earlier draft needed about 305 runs. The savings come from three changes: variant b computed offline, one run set shared by development and test PRs, and pilot and measurement runs reused.

## 6. A reusable evaluation pipeline

The pipeline is built once. After that, testing a new member, or a member for a specific field, should take one command and only that member's usage.

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

- **Findings cache key:** dataset, PR, repeat, member id, hash of the member file, reviewer model, effort, and Claude Code version.
- **Adjudication cache key:** finding hash, hash of the adjudicator file, and model.

Consequences:

- **Testing a new member** runs only that member. Every subset that includes it is then scored by combining its findings with the cached findings of the others.
- **Editing one member** re-runs only that member.
- **Editing the adjudicator** re-runs adjudication only, not the members.
- **Any roster**, including the default board, is scored from a full-board run without being run itself.

**Subset scoring:** dedup groups are computed once on the full union of findings.

- A golden issue counts as found when any member in the subset matched it.
- Unmatched findings in the subset count as false positives, with duplicates collapsed through the union's groups.

### 6.3 Datasets share one format

```
eval/datasets/<name>/
  prs.jsonl       {id, repo, base, head, intent}
  golden.jsonl    {pr_id, comment, severity, category}
  split.json      frozen development / test assignment
  SOURCE.md       where the data comes from, license, pinned commit
```

- `martian`: an adapter that converts the Martian offline set into this format, pinned to one commit.
- **Domain datasets**, built with the method Qodo and Macroscope use:
  1. Find commits that fixed a bug.
  2. Trace each bug back to the commit that introduced it, and use that commit as the review task.
  3. Add control commits with no known bug, so precision can be measured.

  20–30 cases is enough to start. Small domain datasets are also cheap to run.
- Categories extend Martian's taxonomy. A domain dataset may add its own categories, such as `coordinates` or `genome-build`.

### 6.4 Every run is reproducible

Each run records: dataset commit, PR id, variant, roster, member file hashes, adjudicator hash, reviewer model, effort, Claude Code version, judge models and judge prompt version, cost, time, usage percentages, and which account ran it.

Results from different reviewer models or judges are never pooled. Results from different accounts are pooled, because with the same pinned version and the clean setup (Phase 0), the account doesn't change the model's output.

### 6.5 Member cards

`eval report --member <id>` writes a card for one member, per dataset:

- Unique true positives and false positives added.
- Precision and recall per category.
- Usage share.
- Its marginal effect on the default board.

Promotion from candidate to optional to default uses the same rule as Phase 4, on development PRs only.

### 6.6 Protect the test split

The test PRs are used only for release comparisons, and each use is logged. Repeated peeking turns them into a second development split and inflates the scores.

### 6.7 Testing a new member, end to end

1. Add `members/<id>.md` with `status: candidate`.
2. Run `eval run --dataset <name> --split dev --members <id>`. Only the new member runs; everything else comes from the cache.
3. Run `eval report --member <id>`.
4. If the card meets the promotion rule, change `status`, and update `roster.json` when it joins the default board.

## 7. Usage limits

### 7.1 The two accounts

| | Account A: Pro | Account B: Team, Standard seat |
|---|---|---|
| Usage per 5-hour session | 1× (baseline) | 1.25× Pro |
| Weekly limit | Yes, per person | Yes, per person |
| Sonnet 5.5 / Opus 5.5 in Claude Code | Included | Included |
| Fable | Usage credits only | Usage credits only |

Combined capacity is about 2.25× one Pro plan. Anthropic does not publish limits as token counts, so the real capacity is measured (7.2).

Both accounts also carry their owners' daily work, so the experiment must not use the full weekly limit.

### 7.2 Measure, then plan

In Phase 3 step 2, record the session and weekly usage percentages shown by `/usage` before and after each run, while nothing else uses the account. Then:

> runs per week per account ≈ (weekly % the experiment may use) ÷ (weekly % per run)

Proposed ceiling: the experiment uses at most **50% of each account's weekly limit**. The rest stays free for daily work.

If ~160 runs don't fit within about 3 weekly resets under that ceiling, fall back to the minimum option (~80 runs). If even that doesn't fit, reduce the PR count. Check with both collaborators before using usage credits. Account B can also move from a Standard to a Premium seat; discuss this if the pilot looks promising.

### 7.3 Splitting runs between accounts

- **Split by PR, not by variant.** Each account runs both variants for its assigned PRs, so a and c on the same PR share one machine and one environment.
- **Assign PRs about 5 : 4 (B : A)**, matching the 1.25× ratio. Rebalance after the usage measurement.
- **Schedule runs across 5-hour sessions** and spread them over the weekly resets, preferably at times the account isn't needed for other work.

## 8. Repository layout (target)

```
board-review/
  docs/PLAN.md                 this file
  skill/board-review/          the skill (section 4)
  eval/
    datasets/<name>/           shared dataset format (section 6.3)
    runner/                    worktrees, claude -p runs, caching, usage-limit handling
    scoring/                   wraps Martian's extract/dedup/judge steps; subset scoring
    reports/                   member cards and comparison reports (committed)
    results/                   raw run output (git-ignored; shared as release assets when needed)
```

## 9. Working together

- Work on branches and merge through pull requests.
- Commit summaries and reports; keep raw run output out of git.
- Never commit API keys or `.env` files.
- Raw results from the two accounts are merged through the shared cache format (section 6.2), not by hand.

## 10. Decisions

**Settled:**

- Reviewer model: Claude Sonnet 5.5 at `medium` effort, for every variant.
- Accounts: A (Pro) and B (Team Standard), runs split by PR at about 5 : 4 (B : A).
- Usage ceiling: at most 50% of each account's weekly limit, unless both collaborators agree otherwise.
- Judges: Martian's extraction, dedup, and judge steps, unchanged, called through an OpenAI-compatible endpoint (5.1).
- Development judge: `deepseek-flash` on account A's free local endpoint, subject to Gate 1b.
- Final judge panel: the 3 local models (free). The paid published judges are the backup (5.1).
- Gate 2 metric: board findings labeled CONFIRMED + PLAUSIBLE (Yara's proposal, approved).
- Pinned Claude Code version: 2.1.317 on both machines.

**Open:**

1. **Who builds which part** of Phases 1 and 2.
2. **Gate 1's paid judge:** which published judge and provider to use for the one-off check (a few dollars).

## 11. Known limits

- **Training leakage:** Martian PRs come from well-known public repositories, so the models may have seen them.
- **No test execution:** the benchmark repos aren't built locally, so CONFIRMED rests on code traces, not failing tests.
- **Web applications only:** results on Martian don't transfer automatically to other fields. That is why domain datasets exist.
- **Variable intent text:** the intent auditor depends on PR descriptions, and their quality varies.
- **Few repeats:** 2 repeats make each comparison noisier, so only larger differences will be clear.

## References

- Martian Code Review Bench: https://github.com/withmartian/code-review-benchmark
- ironbee raw-LLM harness on Martian: https://github.com/ironbee-ai/code-review-benchmarks
- PR-AF: https://github.com/Agent-Field/pr-af
- Claude Code `/code-review`: https://code.claude.com/docs/en/code-review-local
- Claude Code with Pro and Max plans: https://support.claude.com/en/articles/11145838-using-claude-code-with-your-pro-or-max-plan
- Team plan: https://support.claude.com/en/articles/9266767-what-is-the-team-plan
- Fable models by plan: https://support.claude.com/en/articles/15424964-claude-fable-models-on-your-plan
- Debate or Vote (NeurIPS 2025): https://neurips.cc/virtual/2025/poster/116557
- OpenAI Codex plugin for Claude Code (adversarial review prompt): https://github.com/openai/codex-plugin-cc
