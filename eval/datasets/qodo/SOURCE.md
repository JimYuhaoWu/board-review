# Qodo PR-Review-Bench

- **Source:** https://huggingface.co/datasets/Qodo/PR-Review-Bench (file `git_code_review_bench_100_w_open_prs.jsonl`)
- **Pinned revision:** `a73957c450a70693a743260e5637fffc44625f16` (2026-02-03)
- **License:** MIT
- **Contents:** 100 pull requests from 8 open-source repositories (cal.com 16; Ghost, dify, firefox-ios, prefect, tauri 13 each; aspnetcore 10; redis 9), with 580 injected issues.
- **Converter:** `eval/runner/qodo.py`; `uv run python -m eval.runner golden --dataset qodo` and `fetch --dataset qodo`.

## How the PRs were made

Each item is an open PR in a fork under https://github.com/agentic-review-benchmarks. Its base branch starts at the upstream PR's parent. Its head is the upstream PR's commits plus one commit, "update pr", that injects the issues. So the issues are planted, not historical bugs.

## Conversion

| Qodo | Shared format |
|---|---|
| `pr_url_to_review` | `url`; the id is `<repo>-<fork PR number>`, with the repo name lowercased and `.` or `-` turned into `_` (`cal_com-3`) |
| PR body, `**Original PR Title**` and `**Original PR Description**` | `intent`. The benchmark title ("Code Review Bench PR #...") is dropped, so neither variant is told it's a benchmark. |
| PR body, `**Original PR URL**` | `original_url` |
| issue `title`, `description`, `file_path`, lines, `rule_name` | `comment`: "title. description (file:start-end)", plus "Repository rule: ..." for rule violations. 36 issues have no file or lines. |
| issue without `rule_name` (309) | category `bug`: functional bugs, counted by the Core profile |
| issue with `rule_name` (271) | category `rule`: breaches of a repository rule that Qodo mined from the codebase (`rules_for_repo.jsonl`). Counted only by the All profile (`eval/scoring/profiles.py`). |
| (no severity) | `Unrated` |

Every PR has 2 to 5 functional bugs (3.1 on average) and 5.8 issues in all.

**The rules are in the repositories.** In every fork, a commit by Qodo ("Add AGENTS.md with compliance rules for PR analysis") replaced `AGENTS.md` with the mined rules, in the base commit. In Ghost, cal.com and dify, `CLAUDE.md` holds the same rules too. Neither file is loaded automatically: the runner's `--setting-sources user` keeps `CLAUDE.md` out, and Claude Code doesn't load `AGENTS.md`. But a reviewer can open them, and `/code-review` looks for `CLAUDE.md`. So rule recall partly measures whether a variant reads these files. That is one more reason the Core profile, which decides the gate, leaves rule violations out. They also favour a style-minded member. Every PR records the files in `agent_files`.

## Known leaks, out of reach of the reviewers

- Five Ghost PRs (1, 2, 3, 4, 8) carry a July 2026 comment from an outside account that lists the injected issues as "resolved".
- The same organization hosts other tools' reviews of these PRs, in `<repo>-combined-<tool>` repositories.

The runner works on a local clone with WebFetch and WebSearch blocked and only local read-only commands allowed (`eval/runner/run.py`), so a reviewer can't read either. The PRs are kept.

Checked on 2026-11-05: all 100 PRs are open, each head is in its own fork, and all 100 bodies have the three "Original PR" fields.
