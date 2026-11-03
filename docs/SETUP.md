# Phase 0: machine setup

Each collaborator completes this checklist on their own machine. Then tick your column in the status table at the bottom, in a pull request.

## 1. Claude Code 2.1.288 or later

```bash
claude update
claude --version
```

Both machines must run the **same version** during the measured runs (Phases 3–5). Agree on the version before Phase 3, then stop updating until Phase 5 is done.

## 2. Never set `ANTHROPIC_API_KEY` in the shell that runs the benchmark

When `ANTHROPIC_API_KEY` is set, Claude Code bills the API key instead of your Pro or Team plan. Benchmark runs must use the plan.

```bash
echo "${ANTHROPIC_API_KEY:-not set}"   # must print: not set
```

## 3. Python 3.11+ and uv

The Martian pipeline uses [uv](https://docs.astral.sh/uv/) to manage its Python environment.

```bash
python --version   # 3.11 or later
uv --version
```

If uv is missing, install it with one of these:

```bash
winget install --id=astral-sh.uv -e      # Windows
pip install uv                            # any OS with Python
```

## 4. Git line endings (Windows)

Benchmark clones must not convert line endings. Otherwise CRLF conversion makes whole files appear changed in the diff. The harness sets this for each clone it creates. For any clone you make by hand:

```bash
git -c core.autocrlf=false clone <url>
```

## 5. Judge endpoint and key

The judge is an LLM that decides whether a review comment matches a golden issue. It is called through an **OpenAI-compatible endpoint** (explained below) and billed per token. Plan usage does not cover it.

1. Pick a provider (see "Which provider" below).
2. Copy `.env.example` to `.env` at the repo root and fill in the key. `.env` is git-ignored. Never commit it.

### What "OpenAI-compatible endpoint" means

OpenAI's API defined a request format that became a de facto standard:

```
POST <base URL>/chat/completions
Authorization: Bearer <API key>
{"model": "<model id>", "messages": [{"role": "user", "content": "..."}]}
```

Many providers accept exactly this format, including providers that serve non-OpenAI models. Code written for that format, such as the official `openai` Python client used by the Martian judge, works with any of them. You change three settings, never the code:

| Setting | Meaning | Example |
|---|---|---|
| `MARTIAN_BASE_URL` | Where requests go | `https://openrouter.ai/api/v1` |
| `MARTIAN_API_KEY` | Your secret key for that provider | `sk-or-...` |
| `MARTIAN_MODEL` | Model id, in that provider's naming | `anthropic/claude-sonnet-4.5` |

The variables keep the `MARTIAN_` prefix because the benchmark's own scripts read those names.

### Which provider

| Provider | Base URL | Models | Notes |
|---|---|---|---|
| OpenRouter | `https://openrouter.ai/api/v1` | Claude, GPT, and many others with one key | Prepaid credit. The ironbee harness used it. Supports the full 3-judge panel. |
| Martian gateway | `https://api.withmartian.com/v1` | Many models with one key | The benchmark's default. Supports the full 3-judge panel. |
| Anthropic, OpenAI compatibility | `https://api.anthropic.com/v1/` | Claude only | Uses an Anthropic Console API key. No GPT judge, so it can't run the full panel alone. |
| OpenAI | `https://api.openai.com/v1` | GPT only | No Claude judge, so it can't run the full panel alone. |

Recommendation: **one OpenRouter key**, because one key covers the whole judge panel (Claude Opus 4.5, Claude Sonnet 4.5, GPT-5.2). Check the provider's model list for the exact model ids before the first run.

If you use an Anthropic Console key for the judge, store it only as `MARTIAN_API_KEY` in `.env`, never as `ANTHROPIC_API_KEY` (see step 2).

Judge cost is measured in the pilot. Set a spending limit at the provider before the first run.

## 6. Permission to run the benchmark pipeline

Claude Code's auto mode can block running code from an external repository. Before Phase 2, allow the benchmark's Python steps, for example with a project permission rule for `uv run python -m code_review_benchmark.*`. Ask Claude Code to add the rule with the `update-config` skill, or approve each run when prompted.

## 7. Benchmark source

The Martian benchmark is pinned in [`eval/datasets/martian/SOURCE.md`](../eval/datasets/martian/SOURCE.md). Every run uses that commit.

## Status

| Step | Account A (Pro) | Account B (Team Standard) |
|---|---|---|
| 1. Claude Code ≥ 2.1.288 | ✅ 2.1.317 | ☐ |
| 2. `ANTHROPIC_API_KEY` not set | ✅ | ☐ |
| 3. Python ≥ 3.11 and uv | Python 3.14.5 ✅, uv ☐ | ☐ |
| 4. Git line endings | handled by the harness | handled by the harness |
| 5. Judge key in `.env` | ☐ | ☐ (only if this account runs the judge) |
| 6. Pipeline permission | ☐ | ☐ |
