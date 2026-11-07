# Board Review: design and evaluation plan

Status: **approved, not started**. Phase 0 (setup) is next.

## 1. Goal

Build a Claude Code skill, `/board-review`, that reviews code after a long coding session with minimal supervision.

The skill runs a **board** of specialist reviewers. The user chooses which members sit on the board before each run. An adjudicator then labels every finding as CONFIRMED, PLAUSIBLE, or REFUTED, based on evidence, not on a vote.

We need data to answer two questions:

1. Which members belong on the **default board**, and which stay **optional**?
2. Does the board beat Claude Code's built-in `/code-review`?

The two boards have different jobs (decided 2026-11-04):

- The **full board** is for recall. Optional members exist to find more in their field, such as performance in memory-limited code, so the board with every member must find the most: its recall must beat `/code-review`'s. Precision is the verifiers' job.
- The **default board** is the everyday board: the best F1 for its cost, selected by the Phase 4 rule.

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
| `nitpicker` | Nitpicker | Text that is wrong: names, docs, comments, messages, conventions | style 10, doc_defect | optional |
| `line-reader` | Line reader | Every changed line in order: won't parse or run, wrong conditions, variables, indices | bug | added 2026-11-04 |
| `regression` | Regression checker | Old behavior the change drops: defaults, migrations, removed or reordered steps | bug, data, api | added 2026-11-04 |
| `boundary` | Boundary checker | Values crossing serialization, storage, files, processes, and library or browser APIs | bug, data, api | added 2026-11-04 |
| `test-auditor` | Test auditor | Tests that can't catch what they claim to | test_gap | added 2026-11-04 |

The last four were added after two pilot attempts (section 5, Phase 3). The topic members missed ordinary line-level bugs that no topic owns, and several members reported the same obvious bug. The new members are defined by **method** (read every line, compare old and new, follow values across boundaries, try to break the tests) rather than by topic, so their attention lands on different lines.

Domain members, such as a member for bioinformatics pipelines, can't be measured on Martian because Martian contains only web applications. They are tested on domain datasets (section 6.3).

## 4. Skill layout

The skill ships as a plugin named `board-review`, so benchmark runs can load it with `--plugin-dir` (Phase 0). The command is `/board-review:review`.

```
plugin/
  .claude-plugin/plugin.json
  agents/
    board-member.md        the contract every member follows; tools limited to Read, Grep, Glob, Bash
    board-verifier.md      evidence rules for CONFIRMED / PLAUSIBLE / REFUTED
  skills/review/
    SKILL.md               orchestrator: scope → intent → roster → dispatch → group → adjudicate → report
    members/<id>.md        one brief per member (section 6.1)
    roster.json            named rosters: "default", "full"; Phase 4 fills in "default"
    schema.json            member, verifier, and final output schemas
```

Members run as the plugin agent `board-review:board-member`, and verifiers as `board-review:board-verifier`. Neither has the Edit or Write tool, but both have Bash, which can write files. So read-only rests on their instructions and on the session's permission prompts, not on the tool list. Benchmark runs enforce it with a read-only allowlist and `--permission-mode dontAsk` (Y4).

How the user picks members:

- `/board-review:review --members default|full|intent,edge,...` picks them on the command line.
- With no argument in an interactive session, the skill shows a multi-select list with the default members pre-ticked.
- Benchmark runs always pass the argument.

## 5. Evaluation plan

### 5.1 Fixed settings

- **Benchmark:** Martian Code Review Bench, offline set, pinned to one commit.
- **Reviewer model: Claude Sonnet 5.5 at `medium` effort, for every variant.** Sonnet 5.5 runs on both accounts within plan limits, while Fable runs on usage credits on both (section 7). In the ironbee raw-model results, Sonnet 5.5 tied for the top group at about half of Opus 5.5's cost. Higher effort did not raise Claude review scores there.
- **Variants:**
  - **a:** built-in `/code-review medium --max-findings all <base>..<head>`.
  - **c:** full board, `/board-review:review --members full --max-findings 8 --format json <base>..<head>`. The per-member budget is passed explicitly, so a later change to the skill's default can't change the experiment.
  - **Intent:** both variants get the same text, the PR's title and description from `prs.jsonl`, as Martian's published tools did. The board gets it with `--intent-file` (a file, because PR descriptions can contain `--flags`). `/code-review` gets it as text after the target, if a smoke-repo test shows that this doesn't change what it reviews; otherwise neither variant gets it, and both see only commit messages (Yara, review of PR #8).
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
    - **No hard quota, but the cluster throttles a long queue.** On 2026-11-03 the key got HTTP 429 ("at most 1 request per 70 minutes") after three smoke-test review runs through Claude Code and about 340 judge calls in one evening. The HPC administrators confirmed there is no hard quota: the cluster slows requests down when too many are queued, and they suggest running at a low rate or when few people are using it.
    - **So judging runs slowly, preferably at night.** The harness starts at most 10 calls a minute, one at a time by default (`--rate`, `--concurrency`). After a 429 or a lost connection, it pauses (1 minute, doubling to at most 15) and repeats the call, and it stops cleanly after 3 hours without success (`--max-wait`). Every finished call is cached, so a stopped session resumes where it left off. Account A connects through a VPN, which can drop; if that happens often, judging moves to a machine on the campus network.
    - **Review runs through Claude Code** (smoke tests on the local models) make many calls quickly. Run them only at quiet times, never alongside judging.
    - **Time, not money, is the cost.** About 10,000 calls per judge for the full run set (rough estimate) is about 17 hours at 10 calls a minute, so a few nights per judge.
    - Key test (2026-11-03): all 3 models answered a judge-style prompt with valid JSON, with `temperature: 0` accepted, in 0.7–2.5 s. The response reports only the alias (`deepseek-flash`, `glm-flash`, `Qwen`), not the version, and the provider's model list carries no version either (every model shows the same fixed `created` date). The harness logs the list entry at each judging session, but it can't reveal a silent model update; J3 adds a drift check that re-judges a fixed sample of pairs at each session. Whether temperature 0 gives repeatable verdicts is checked in Gate 1b by judging the same pairs twice.

### Phase 0: setup

- **One pinned Claude Code version on both machines: 2.1.317.** It must be 2.1.288 or later, which `--max-findings` needs. Install it as a standalone CLI with `claude install <version>`, log it in to the account's plan, and set `DISABLE_AUTOUPDATER=1`. Don't run benchmarks with the copy bundled in the desktop app: it updates with the app and isn't logged in when run on its own.
- **A clean Claude Code setup for benchmark runs**, the same on both machines. A personal setup (user CLAUDE.md, auto-memory, hooks, MCP servers, plugins, skills) changes what the reviewer writes and adds usage to every run. Tested on 2.1.309:
  - **Use:** a dedicated, empty config directory (`CLAUDE_CONFIG_DIR`, logged in once), plus `--plugin-dir` to load the board-review skill from this repo. The user's skills are gone and the built-in `/code-review` stays. The skill is packaged as a plugin, and it shows up as `/board-review:review` (section 4).
  - `--safe-mode` doesn't work: it hides skills loaded through `--plugin-dir`.
  - `--bare` doesn't work: it accepts only an API key, not a plan login.
  - The runner's `--setting-sources user` also keeps a benchmark repo's own `CLAUDE.md` out of the context (tested on 2.1.317 with a canary word; `AGENTS.md` isn't loaded either way). A reviewer can still open either file with a tool, and `/code-review` does look for `CLAUDE.md`. Two Martian PRs have one (`agent_files` in `prs.jsonl`).
- A judge API key for an OpenAI-compatible endpoint, such as the Martian gateway or OpenRouter. It stays in a local `.env` and is never committed.
- Allow Claude Code to run the benchmark's Python pipeline (MIT-licensed). Auto mode may block external code until a permission rule allows it.
- On Windows, set `core.autocrlf=false` in every benchmark clone. Otherwise CRLF conversion makes whole files appear changed in the diff.

### Phase 1: build the skill

Write the orchestrator, the eight members, the adjudicator, and the schema from sections 3 and 4.

Develop against 1–2 small benchmark PRs only. Full runs start in Phase 3.

### Phase 2: build the harness

1. **Fetch.** Clone each benchmark PR's fork and record the base and head commits. The Martian golden file flags 5 of the 50 PRs as unusable: 4 because "reviewed commit is not in the repo", 1 because it mixes many PRs. Confirm the real runnable count; PR-AF reports 38. **Result: 45 runnable** (Y1); the 5 unrunnable are exactly the flagged PRs.
2. **Run.** Each (variant, PR, repeat) gets a fresh worktree at the head commit and one `claude -p ... --output-format stream-json --verbose` run, with the settings in 5.1. The model and effort are passed with `--model` and `--effort`, and `/code-review`'s level is typed in the command, so nothing falls back to a default or a remembered setting.
   - The runner reads the setup from the run's `init` event: Claude Code version, model, MCP servers, plugins, and skills. A run whose setup differs from the expected one is discarded.
3. **Prevent leakage.**
   - Edit, Write, WebFetch, and WebSearch are disallowed, so a reviewer can't look up the upstream fix.
   - The working directory is outside the benchmark repo, so the reviewer never sees the golden comments.
4. **Survive usage limits.**
   - A run that stops at a usage limit is marked `failed: usage-limit`, never recorded as an empty review.
   - The runner stops for that account and resumes after the reset.
   - A board run is one `claude -p` call, so a resumed run re-runs the whole (variant, PR, repeat). Running the same command again skips finished runs.
5. **Score every variant the same way.** Both variants report structured findings (the board's JSON; `/code-review`'s `ReportFindings` call). Each finding is rendered as its location plus its one-sentence statement and is one candidate, so findings keep their member and label for subset scoring. The board counts once per group of duplicates, as its report shows it. Martian's dedup and judge steps run unmodified. Martian's extraction step is skipped for structured findings, which are one issue each: on the smoke repo, extracting them one at a time split compound sentences into extra false positives. Martian's own whole-review extraction is scored too, as a check. Details in `eval/scoring/README.md`.
6. **Score board runs three ways:** CONFIRMED only, CONFIRMED + PLAUSIBLE, and the raw union before adjudication.
7. **Log for every run:** cost (`total_cost_usd`), wall time, tokens, which conditional members fired, the plan usage percentage before and after (section 7.2; only approximate if the account is used for other work at the same time), and the run metadata in section 6.4.

### Phase 3: measure, pilot, decide whether to continue

**Step 1, freeze the split.** Before any results are scored, split the runnable PRs into 15 development PRs and 30 test PRs, 3 development PRs per repo, with a recorded seed. The split also fixes the 10 pilot PRs. It is in `eval/datasets/martian/split.json` and never changed.

**Step 2, measure usage (2 runs).** On the largest pilot PR, run variant a once and variant c once, while nothing else uses the account. `/usage` counts all work on the account, so other work would inflate the numbers. Record session and weekly usage before and after each run (section 7.2). This decides the number of repeats. These 2 runs also count as that PR's pilot runs.

**Step 3, pilot.** 10 development PRs (2 per repo), including the PR from step 2, variants a and c, 1 repeat. The pilot runs are kept as repeat 1 for those PRs. All 10 run on account B (Yara, 2026-11-03), so step 2 measures account B only. Two gates:

- **Gate 1, judge sanity:** re-judge the stored `claude-code` row with one of the published judges, and compare the result with that judge's published score. This checks our copy of the judge pipeline. It needs about 700 judge calls (50 reviews × ~14 pairs), a few dollars on a paid API.
  - **Judge:** Claude Sonnet 4.5 through OpenRouter (`openrouter:anthropic/claude-sonnet-4.5`, snapshot 2025-09-29, the same as Martian's), about $2.
  - **Pass rule:** for at least 90% of golden comments, found / not found agrees with Martian's published Sonnet 4.5 verdicts on the same reviews (Martian's three judges agree with each other on 95%), and F1 (Core) is within 3 points of the published 38.7%.
  - Command: `uv run python -m eval.scoring gate1 --judge openrouter:anthropic/claude-sonnet-4.5 --martian-dir <Martian clone>/offline`.
- **Gate 1b, local-judge agreement** (free; judge calls only, no review runs): each local judge re-judges the stored rows of several published tools. Its ranking of those tools must agree closely with the published rankings. A local judge that fails isn't used.
  - **Tools:** 7 that span the published range: cubic-v2, augment, devin, macroscope, claude-code, codeant-v2, kg. That is all 50 PRs each, about 4,700 calls per judge, or one night at 10 calls a minute.
  - **Pass rule:** the local judge's mean Kendall τ (F1 ranking, Core profile) with the three published judges is at least the lowest τ between two published judges.
  - **Repeat check:** 200 of the judge's cached prompts are asked again. The agreement rate shows how repeatable temperature 0 is, and becomes the baseline for the drift check at later sessions.
  - Command: `uv run python -m eval.scoring gate1b --judge <model> --martian-dir <Martian clone>/offline`; the report goes to `eval/reports/gate1b-<model>.md`.
- **Gate 2, early stop.** History: the first rule asked for a higher F1 than `/code-review`. The board failed it twice (42.0% vs 43.5%, then 38.2% vs 43.5% after the Saboteur and Nitpicker rewrites; `eval/reports/pilot-gate2-deepseek-flash.md`). Both times precision was level and recall was the gap. The design was rethought (section 1, section 3), and the rule below replaces the old one. It was written on 2026-11-04, before any run of the new design:
  - **Rule:** the full board's **recall** (Core profile, findings labeled CONFIRMED or PLAUSIBLE, as the report shows them) is higher than `/code-review`'s on the 10 pilot PRs, judged by the development judge.
  - **Repeats:** 2 per PR for both variants. With 1 repeat, a swing of 4 golden comments moves the result by about 9 points of recall.
  - **Reported, not deciding:** the paired-bootstrap CI for the recall difference, precision, F1, the grouping loss, and the member cards.
  - **Command:** `uv run python -m eval.scoring gate2 --dataset martian --judge deepseek-flash --split dev`. It prints PASS, FAIL, or NOT DECIDED (too few repeats).
  - **If it fails:** stop. The next step is a written analysis and a decision by both collaborators, not another tuning round on the pilot.
  - **If it passes:** Phase 4.
  - **Result (2026-11-05): FAIL.** Y8 runs, 10 pilot PRs, 2 repeats each, judged by `deepseek-flash`:

    | Core profile | Precision | Recall | F1 |
    |---|---|---|---|
    | Full board (`board:full:cp`) | 32.0% | 67.4% | 43.4% |
    | `/code-review` | 29.6% | 70.9% | 41.8% |
    | Full board, every finding counted | 24.7% | 72.1% | 36.8% |

    - ΔRecall −3.5 points (95% CI −16.2 to +10.5), ΔF1 +1.7 (−6.9 to +10.5). Grouping lost 2 golden comments, down from 5.
    - The gap is in the `bug` category (14.5 of 22 against 17 of 22). The board found more test gaps, style, and doc defects.
    - The pilot has 43 Core golden comments, so a 3.5-point gap is about 1.5 comments, and the CI can't separate the two variants.
    - Following the rule above, the briefs are not tuned. The members that could be tuned later are listed under "When members are tuned".
    - **Next step:** Gate 2 on Qodo, below.
- **Gate 2 on Qodo (external check).** Written on 2026-11-05, after the Martian result and before any Qodo run or split. It asks the same question as Gate 2 v2 on a dataset that no brief was written from, with about 1.7 times as many functional golden issues.
  - **Dataset:** Qodo PR-Review-Bench at the pinned revision (`eval/datasets/qodo/SOURCE.md`). The sample is 3 PRs per repository, 24 in all, drawn by `split --dataset qodo --dev-per-repo 3 --pilot-per-repo 3 --seed 20261105` once `fetch` has recorded the runnable PRs. The sample is the development and pilot set; the other PRs are the test set and stay unused.
  - **Rule:** the full board's **recall on functional bugs** (Core profile: category `bug`; findings labeled CONFIRMED or PLAUSIBLE) is higher than `/code-review`'s, judged by `deepseek-flash`.
  - **Repeats:** 2 per PR for both variants (96 runs).
  - **Reported, not deciding:** recall on all issues (All profile, with the repository-rule violations; Qodo put the rules in each fork's `AGENTS.md`, and `CLAUDE.md` in three repositories, so this partly measures which variant reads them), the paired-bootstrap CIs, precision, F1, the grouping loss, the per-repository recall, and the member cards.
  - **Judge check:** after judging, Jim labels about 60 of the judge's match / no-match verdicts on Qodo without seeing them, stratified by verdict. Agreement is reported. If it is below 85%, the result is NOT DECIDED until the disagreements are understood.
  - **Command:** `uv run python -m eval.scoring gate2 --dataset qodo --judge deepseek-flash --split dev`, and `report --profile all` for all issues.
  - **If it passes:** the board's recall advantage holds outside the data it was designed on. Both collaborators then decide how Phase 4 continues.
  - **If it fails:** the full board doesn't beat `/code-review` on recall on either dataset. Stop, with a written analysis and a decision by both collaborators. No tuning round.
  - **Result (2026-11-06): FAIL** (`eval/reports/gate2-qodo-deepseek-flash.md`). Functional-bug recall: board 69.4%, `/code-review` 77.1%, ΔRecall −7.6 points (95% CI −14.9 to −0.7). Precision 55.9% against 40.5% (+15.4, CI +7.8 to +23.5), F1 61.9% against 53.1% (+8.8, CI +1.9 to +16.0).
    - Every finding the members reported reaches only 70.1% recall, so the members' coverage limits recall, not the verifier.
    - Judge check: 60.0% agreement on the first pass, 96.7% after Jim re-judged the 24 disagreements with the full texts (the short sheet showed only the issue titles). The judge never matched a pair Jim called different.
    - **Next step:** Gate 3, below. Plan B (a high-recall "cautious" member) was considered and dropped: writing it from these misses would be tuning, and the misses are issues no member reported.
- **Gate 3, `/code-review` as a member (Plan A).** Written on 2026-11-06 and agreed by both collaborators the same day, before any Gate 3 run or PR draw.
  - **Why:** on the Qodo sample, every finding the 12 members reported reaches 70.1% functional-bug recall, below `/code-review`'s 77.1%, and only one golden issue was lost by the verifier. Recall is limited by what the members notice, not by the filtering. The board's measured strength is precision (+15.4 points on Qodo). Adding `/code-review` as a member would combine its recall with the verifier's precision.
  - **The baseline is `/code-review` run twice, not once.** On the same Qodo runs, merging two `/code-review` runs gives 84.7% recall, above the 82.6% of one board run plus one `/code-review` run. A gain over a single `/code-review` run could come from a second sample alone. So Plan A is compared with a second sample of `/code-review`, which costs about $0.10 per PR against about $0.90.
  - **Variants:**
    - **A1, `/code-review` ×2:** two independent `/code-review` runs on the same PR, their findings merged into one review by `merge` (`eval/scoring/pipeline.py`, `merge_records`). Martian's strict dedup step runs once over both runs' findings. No verdict is new.
    - **A2, board + `/code-review`** (variant `board-cr`): first a `/code-review` run in its own session, as for A1; then the full board with `--extra-findings`, holding that run's findings as the 13th member `code-review`. They join the other members' findings before grouping and verification, like any member's. The 12 briefs are not changed. A2's `/code-review` runs are **independent** of A1's: none is shared, and each is stored inside its `board-cr` run's folder, never as a `code-review` run. The findings file is made by `code_review_member_output` (`eval/scoring/data.py`), fixed before any Gate 3 run: claim = the finding's summary, evidence = its failure scenario, severity High for every finding (so each gets the second verifier), confidence 0.8 if `/code-review` marked it CONFIRMED, else 0.6.
  - **One counting rule for both variants** (views `code-review-x2` and `board-cr:full:cp:issues`): every finding the variant reports is kept (for A2, those labeled CONFIRMED or PLAUSIBLE), Martian's strict dedup groups them, and each group counts once: a hit if any of its findings matches a golden issue, otherwise one false positive. No finding is picked over another, so neither variant loses an issue to its merging; the board's own grouping is reported as `board-cr:full:cp`, not used. Tested on the Gate 2 Qodo runs before any Gate 3 run: precision / recall / F1 is 33.7 / 84.7 / 48.2% for A1, 55.4 / 71.5 / 62.4% for the full board, and 40.7 / 77.1 / 53.2% for one `/code-review` run. A first version, which kept only the earlier run's finding of each group, lost 5 of the 61 issues the two runs found, and was dropped.
  - **PRs:** 24 PRs not used before, 3 per repository, drawn from the 76 Qodo test PRs with seed 20261107, written to `eval/datasets/qodo/gate3.json`. They are logged as a release use of the test split (6.6) and leave it. Fixed before any run.
  - **Repeats:** 2 per PR and variant. Per PR: 4 `/code-review` runs for A1 (repeats 1–4; A1 repeat 1 merges runs 1 and 2, repeat 2 merges 3 and 4), and 2 `board-cr` runs, each with its own `/code-review` run. 24 PRs: 144 `/code-review` sessions and 48 board sessions.
  - **Merging A1:** `merge --dataset qodo --judge deepseek-flash --split test --release "Gate 3" --pairs 1,2 3,4` after judging.
  - **Settings:** as Gate 2 on Qodo: Claude Code 2.1.317, Sonnet 5.5 at `medium`, the plugin at a recorded commit, account B runs, `deepseek-flash` judges on account A, Core profile, board findings labeled CONFIRMED or PLAUSIBLE. The judge check of Gate 2 on Qodo must be settled first, since this gate uses the same judge on the same dataset.
  - **Rule:** Core-profile **recall** decides, A2 − A1, paired bootstrap over PRs (95% CI); **F1** is the guardrail: A2's F1 must be at least one `/code-review` run's (`code-review:issues`, the mean over all `/code-review` runs). Recall decides because the question is whether the members find what a second `/code-review` sample doesn't; on F1 alone, A1 is an easy bar, since a second run adds mostly false positives (48.2% against 53.2% on the Gate 2 runs).
    - Recall CI above 0 and the guardrail met: **board + `/code-review` wins**; the members add value on top of `/code-review`. Both collaborators then decide how Phase 4 continues with A2.
    - Recall CI below 0: **the members aren't needed.** `/code-review` twice finds more, at about a ninth of the cost. Stop, with a written analysis and a decision by both collaborators.
    - Otherwise (the CI spans 0, or the guardrail fails): **no decision.** Both collaborators decide what follows, with the written analysis.
  - **Command:** `gate3 --dataset qodo --judge deepseek-flash --split test --release "Gate 3"` prints WIN, MEMBERS NOT NEEDED, NO DECISION, or NOT DECIDED (too few repeats).
  - **Reported, not deciding:** recall and precision for A1 and A2; a single `/code-review` run (the mean over A1's runs, as in the guardrail); the 13th member's unique finds and marginal recall; the verifier's wrongful refutations of `/code-review`'s findings; cost and time per run; the All profile.
  - **No tuning** between drafting this entry and the result, on either variant.
  - **Run fixes, decided 2026-11-06 after the runs and before any scoring** (no score seen):
    - **An empty `ReportFindings` call no longer hides a reply's findings.** Three `/code-review` sessions out of the 212 recorded called `ReportFindings` with an empty list by mistake, then listed 6 findings in the reply and said those count: A1 `redis-4` r3, and the `/code-review` sessions of A2 `firefox_ios-2` r1 and `aspnetcore-10` r1, whose boards therefore got no `code-review` findings. `code_review_found` now falls back to the reply when the accepted call is empty; no other run changes. The two A2 runs are rerun in full, since their boards never saw the findings; A1's run is re-scored.
    - **`ghost-7` A2 r2: the board runs with an empty extra member.** Its `/code-review` session failed the same way twice: a denied Bash script, then a partial review reporting no defects. The runner read "no findings" as `failed:extra-findings` and never started the board, which in use would still run. A1 keeps its own failed session (`ghost-7` r3, no findings) and still has r4 in its repeat 2; so A2 keeps its failed session too, and its 12 members still run. The runner now passes an empty `code-review` member when a finished `/code-review` session lists no findings, flagged `extra_findings.parsed: false`; a failed session still stops the run. Counting the run as no findings at all was rejected: A1's repeat 2 has 3 findings from r4, so it would have been one-sided.
    - **Ten A1 runs that hit the usage limit** were recorded as `ok` with only the limit notice as their reply; they were rerun before packing, and the runner now catches that notice (#41).
  - **Result (2026-11-07): NO DECISION** (`eval/reports/gate3-qodo-deepseek-flash.md`). Core recall: A2 71.2%, A1 73.3%, ΔRecall −2.1 points (95% CI −7.6 to +3.8). Precision 34.0% against 30.9% (+3.1, CI −0.1 to +6.9), F1 46.0% against 43.5% (+2.5, CI −1.2 to +6.7). The guardrail fails too: A2's F1 is below one `/code-review` run's 46.6%. A2 costs about 9 times A1.
    - Reported: A2 with CONFIRMED findings only reaches 45.7% precision and 58.2% recall (ΔF1 +7.8 against A1, CI +1.4 to +13.6); the default board from Gate 2 reaches 71.9% and 56.2% on these PRs. A2's own `/code-review` sessions found 4.5 recall points less than A1's (CI −8.5 to −0.4) under the same settings, a weak draw found after the result.
    - **Next step:** decided by both collaborators from the report.

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

This rule picks the default board. The **full board** keeps every member whose marginal recall (section 6.5) is above zero on its own field's data. A member is judged only on a dataset that has golden issues in its categories: Martian has no performance issues in the pilot, so the Performance member can't earn or lose its place there.

**Adjudicator checks on the development PRs:**

- **Wrongful refutation rate:** how often a REFUTED finding matches a real golden issue. This is the most important number for unattended use, because each case is a real bug the board discarded.
- CONFIRMED precision must be clearly higher than PLAUSIBLE precision. If it isn't, the labels carry no information.

**Check offline b against real b runs:** once the default board is chosen, run it for real (1 repeat) on the development PRs, and compare its CONFIRMED + PLAUSIBLE F1 with the offline estimate. If they differ by more than one bootstrap standard error, Phase 5 reports b from real runs on the test PRs too.

Both collaborators sign off on the selected default board before anyone scores the test PRs.

### When members are tuned

Tuning a brief on the development PRs is allowed, but each change is an experiment with a written rule:

1. **Trigger.** A member card shows a problem: zero unique golden comments over at least 20 runs, own-category recall clearly below `/code-review`'s, or heavy overlap with another member.
2. **One change set per round,** frozen before the run. The change states a general rule about reviewing; it must not list the development PRs' issues.
3. **Measure** with at least 2 repeats per PR, and compare with the previous version by paired bootstrap on the member's marginal recall and its own-category recall.
4. **Keep, revise once, or retire.** A member that still has zero unique golden comments after one revision leaves the pool.
5. The test PRs are never used for tuning (section 6.6).

**Members that could be tuned later (deferred, 2026-11-05).** The Gate 2 v2 member cards (20 runs each) show these candidates. None is tuned or re-run now: the aim is to settle Gate 2, and a re-run for a marginal gain isn't worth its cost yet. Any later round follows the five steps above.

| Member | What the cards show | Possible direction |
|---|---|---|
| intern, concurrency, contract, regression, boundary | 0 unique golden comments, marginal recall 0.0% | Revise once or retire (step 4). Concurrency found 3 of 4 concurrency issues, but others found them too. |
| edge | 1 unique; F1 contribution −3.6% (CI −5.0 to −1.7), the most negative | Fewer, sharper findings |
| intent | 1 unique; marginal recall −1.2% | Check whether the negative value is a grouping effect (section 6.5) |
| boundary | Missed the two issues its brief names (stale metadata after a write, a type the serializer can't take), 0 of 2 each | Method, not wording: trace each crossing to its reader |
| test-auditor | All of its +3.5% marginal recall comes from one development issue (two golden comments on the same mocked sleep), which its attack list nearly paraphrases | Its value is unproven until a dataset its brief wasn't written from |

### Phase 4b (optional): stronger models for selected roles

Owner: Yara. Uses **development PRs only**, so the test PRs stay untouched.

The main comparison keeps every role on Sonnet 5.5, so a win can't come from a stronger model. This optional experiment asks whether a stronger model **in one role** is worth its usage.

- **Variants**, each treated as a separate member variant with its own cache key (6.2):
  - **The verifier on Opus 5.5.** The verifier sets the labels, and wrongful refutation is the costliest error, so it is the first candidate. It needs adjudication-only runs over the cached findings, with no member re-runs.
  - **The 1–2 members with the most unique true positives** in Phase 4, on Opus 5.5, for example `edge@opus`. Each runs alone.
- **Not tested:** Fable models, which cost usage credits on both accounts.
- **Rule:** a variant replaces the Sonnet version only if:
  - for a member: development F1 improves by more than one bootstrap standard error;
  - for the verifier: the wrongful refutation rate falls, with CONFIRMED precision not lower;
  - and the extra usage fits the ceiling in section 7.
- **Timing:** finish before Phase 5 if the result may change the default board. Otherwise it can run after Phase 5 as a follow-up.
- **Reporting:** if Phase 5 uses an adopted variant, also report b with every role on Sonnet, so the effect of the design and the effect of the model stay separate.
- **Skill support needed:** an optional `model:` field in a member brief, and a `--verifier-model` flag. The orchestrator passes them to the Agent tool's `model` parameter.

### Phase 5: final comparison

- **Variants:** a (`/code-review`) vs. b (default board, computed offline) vs. c (full board), on the test PRs.
- **Judges:** the 3-judge panel from 5.1.
- **Metrics:**
  - Precision, recall, F1, and F2 on Martian's Core and Strict profiles.
  - Recall per category. For example, does the saboteur raise security recall?
  - Usage and wall time per PR.
- **Statistics:** paired bootstrap over PRs.
- **If a and c are too close to call**, add a third repeat on the test PRs only.

With 30 test PRs, only fairly large differences will be significant. A secondary result on all runnable PRs is reported too, flagged as optimistic for b, because b's members were selected on part of that data.

### 5.2 Run budget (45 runnable PRs)

| Step | Runs |
|---|---|
| Phase 3, usage measurement (counted as one pilot PR) | 2 |
| Phase 3, pilot on the other 9 PRs × 2 variants (kept as repeat 1) | 18 |
| Phase 4, repeat 1 on the remaining 35 PRs × 2 variants | 70 |
| Phase 4, repeat 2 on all 45 PRs × 2 variants | 90 |
| Phase 4, real default-board runs on ~15 development PRs (check of offline b) | ~15 |
| **Total** | **~195**, plus judge calls |
| Minimum option: 1 repeat only, plus the b check | ~105 |
| Optional: third repeat on test PRs | +60 |
| Optional Phase 4b: 1–2 members × ~15 development PRs × 2 repeats, plus adjudication-only verifier runs | ~30–60 single-member runs, ~30 adjudication runs |

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

`python -m eval.scoring members --dataset <name> --judge <judge> [--member <id>]` prints a card per member, per dataset (built 2026-11-04):

- **Runs:** how many runs the member ran in. Triggers skip some.
- **Own categories:** the golden comments in the categories its brief targets (its `categories:` frontmatter). The card shows how many the member found, how many `/code-review` found on the same PRs, and the total. This check exposed the Saboteur: 0 of 7, against 6 for `/code-review`.
- **Unique:** golden comments the member found that no other member found.
- **Marginal recall** (the primary member metric, 2026-11-04): board recall minus the recall of the board without the member, with a paired bootstrap over PRs. Positive means the member adds recall.
- **F1 contribution:** the same difference in F1. It matters for the default board.
- **Caveat:** both differences are computed on the report, which shows one finding per group. Removing a member can change which finding heads a group, so while the board over-merges, a member can show a negative marginal recall that is really a grouping effect. `report --views board:full:cp:ungrouped` scores every finding; the difference to `board:full:cp` is the **grouping loss**, which `gate2` prints. On the second pilot it was 5 golden comments.
- **Usage share:** the member's share of the price-weighted tokens of all members and verifiers, read from `stream.jsonl`.
- **The member table** above the cards: golden comments found, found by no other member, false positives added, and precision.

Promotion from candidate to optional to default uses the same rule as Phase 4, on development PRs only.

### 6.6 Protect the test split

The test PRs are used only for release comparisons, and each use is logged. Repeated peeking turns them into a second development split and inflates the scores.

### 6.7 Testing a new member, end to end

1. Add or edit `members/<id>.md`. A new member gets `status: candidate`.
2. Run only that member on the development PRs, with the same repeat numbers as the full runs: `uv run python -m eval.runner run --variant board-full --members <id> --set dev --repeat N`. The runner refuses test PRs, and a member whose brief is unchanged since the full run of the same PR and repeat. The spec is TASKS Y7.
3. Judge the new runs, then read the member's card with `python -m eval.scoring members ... --member <id>`. The card combines the member's new findings with the other members' findings from the full runs. The spec is TASKS J5; this isn't built yet.
4. If the card meets the promotion rule, change `status`, and update `roster.json` when the member joins the default board.

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

**Result, account B (2026-11-03).** The pilot's 20 runs cost $8.16 API-equivalent: $0.07 per `/code-review` run and $0.74 per board run. About $10.7 of work moved the session meter 13 points and the weekly meter about 2 points. So on account B one weekly point is about $5 (between $3.50 and $10, because `/usage` shows whole percents), and a 5-hour session holds about $80. One PR with both variants is about 0.15 weekly points. The remaining ~175 runs need about 15 weekly points (7–20), even if account B runs all of them.

Proposed ceiling: the experiment uses at most **50% of each account's weekly limit**. The rest stays free for daily work.

If ~195 runs don't fit within about 3 weekly resets under that ceiling, fall back to the minimum option (~105 runs). If even that doesn't fit, reduce the PR count. Check with both collaborators before using usage credits. Account B can also move from a Standard to a Premium seat; discuss this if the pilot looks promising.

### 7.3 Which account runs what

- **Account B runs every review** (decided 2026-11-03, after the pilot). The ~175 remaining runs need about 15 weekly points on account B (section 7.2), well inside its ceiling, so the planned 5 : 4 split by PR is dropped. All runs then share one machine and one environment, which also removes the account as a source of variation.
- **Account A judges only.** It receives the runs as zips (section 9) and runs every judge.
- **Schedule runs across 5-hour sessions** and spread them over the weekly resets, preferably at times the account isn't needed for other work.

## 8. Repository layout (target)

```
board-review/
  docs/PLAN.md                 this file
  plugin/                      the board-review plugin (section 4)
  eval/smoke/                  planted-bug repository for mechanics checks
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
- Results reach the judging machine (account A) as one zip per batch: `uv run python -m eval.runner pack --variant both --set pilot --repeat 1` on the running machine, sent by its owner, and extracted at the repo root with `uv run python -m zipfile -e <zip> .`. The zip holds the finished runs only, with a manifest.

## 10. Decisions

**Settled:**

- Reviewer model: Claude Sonnet 5.5 at `medium` effort, for every variant.
- Accounts: A (Pro) and B (Team Standard). Account B runs every review; account A judges (2026-11-03, after the pilot).
- Usage ceiling: at most 50% of each account's weekly limit, unless both collaborators agree otherwise.
- Judges: Martian's extraction, dedup, and judge steps, unchanged, called through an OpenAI-compatible endpoint (5.1).
- Development judge: `deepseek-flash` on account A's free local endpoint, subject to Gate 1b.
- Final judge panel: the 3 local models (free). The paid published judges are the backup (5.1).
- Gate 2 metric: board findings labeled CONFIRMED + PLAUSIBLE (Yara's proposal, approved).
- Gate 2 rule, v2 (2026-11-04): full-board recall above `/code-review`'s, 2 repeats per PR (Phase 3). The full board is for recall, the default board for F1 (section 1).
- Gate 2 v2 failed on Martian (2026-11-05). No member is tuned; the same rule is tested on 24 Qodo PRs, functional-bug recall deciding (Phase 3).
- Gate 2 failed on Qodo too (2026-11-06): the board's precision and F1 are higher, its recall lower. Next is Gate 3, `/code-review` as a member against `/code-review` run twice, recall deciding with an F1 guardrail and one counting rule for both; A2's `/code-review` runs independent of A1's (Phase 3; agreed by both collaborators 2026-11-06).
- Four method-based members added, the grouping rule tightened, and marginal recall made the primary member metric (2026-11-04).
- Intent: both variants get the PR title and description (5.1), with "neither" as the fallback.
- Gate 1's paid judge: Claude Sonnet 4.5 through OpenRouter, with the pass rule in Phase 3.
- Pinned Claude Code version: 2.1.317 on both machines.
- Dataset: all 45 runnable Martian PRs, split 15 development / 30 test with seed 20261103, frozen in `split.json` (Yara, 2026-11-03).

- Work split: see [TASKS.md](TASKS.md). Account A's side builds the skill, scoring, and judge gates. Yara builds the data pipeline, the split, and the runner, and owns the optional Phase 4b.

**Open:**

None at the moment.

## 11. Known limits

- **Training leakage:** Martian PRs come from well-known public repositories, so the models may have seen them.
- **No test execution:** the benchmark repos aren't built locally, so CONFIRMED rests on code traces, not failing tests.
- **Web applications only:** results on Martian don't transfer automatically to other fields. That is why domain datasets exist.
- **Variable intent text:** the intent auditor depends on PR descriptions, and their quality varies.
- **Few repeats:** 2 repeats make each comparison noisier, so only larger differences will be clear.
- **Members designed after seeing development misses.** The four members added on 2026-11-04 were motivated by what the pilot missed. Their briefs state general methods, but the pilot will flatter them. Only the test split or an external dataset can show whether they generalize. Gate 2 v2 confirmed the risk: the Test auditor's whole marginal recall came from the issue its brief was written from.
- **Offline variant b keeps c's labels (Yara, review of PR #8).** Grouping and adjudication run over all members' findings. In a real b run, groups would be smaller, a group's severity could drop below the bar for a second verifier, and verifiers would see fewer co-findings. So offline b is exact for the raw union but only approximate for CONFIRMED and PLAUSIBLE. Phase 4 checks the size of this effect with real b runs on the development PRs.
- **`/code-review` reports at most 32 findings,** even with `--max-findings all`: that is its `ReportFindings` tool's limit. Martian PRs have far fewer golden comments, so the cap should rarely matter.

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
