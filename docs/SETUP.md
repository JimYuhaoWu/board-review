# Phase 0: machine setup

Each collaborator completes this checklist on their own machine. Then tick your column in the status table at the bottom, in a pull request.

## 1. One pinned Claude Code version, with a clean setup

See PLAN Phase 0 for the reasons. Both machines run **Claude Code 2.1.317** from Phase 2 until Phase 5 is done (PLAN section 10).

```bash
claude install 2.1.317       # standalone CLI, not the desktop app's bundled copy
claude --version
```

- Set `DISABLE_AUTOUPDATER=1` in the environment of the shell that runs the benchmark, so the version stays pinned.
- Benchmark runs use a dedicated, empty config directory. Log in to the plan there once:

  ```bash
  export CLAUDE_CONFIG_DIR="$HOME/.claude-bench"
  claude          # log in, then exit
  ```

  Then replace `settings.json` in that folder (`$HOME/.claude-bench/settings.json`) with:

  ```json
  {
    "autoMemoryEnabled": false,
    "syncClaudeAiSkills": false,
    "syncClaudeAiPlugins": false,
    "disableClaudeAiConnectors": true,
    "env": { "DISABLE_AUTOUPDATER": "1" }
  }
  ```

  An empty folder isn't enough on its own. Your claude.ai account can bring things into it:
  - **Skills and plugins enabled on claude.ai** sync into the folder while Claude Code runs. They differ between the two accounts, so runs would no longer match.
  - **claude.ai connectors**, such as Google Drive, would load as MCP servers.
  - **Auto-memory** would carry notes from one review run into the next.

  The `env` line keeps the version pinned, even in a shell where `DISABLE_AUTOUPDATER` isn't set.

  The harness loads the board-review skill with `--plugin-dir`. Your own skills, memory, hooks, and MCP servers stay out of the runs. Don't install anything in this folder.

  Never set `CLAUDE_CONFIG_DIR` permanently. It would replace your normal setup in every terminal. The harness sets it for each run.

  **Windows:** without Git Bash, Claude Code offers a PowerShell tool instead of Bash, and the board's agents (`tools: Read, Grep, Glob, Bash`) get no shell at all. The runner sets `CLAUDE_CODE_GIT_BASH_PATH` to the `bash.exe` of the Git for Windows that provides `git`, and every run's setup check fails if Bash is missing.

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
| DeepSeek | `https://api.deepseek.com` | DeepSeek only (e.g. `deepseek-flash`, `deepseek-v4-pro`) | Cheap, but not needed while the local endpoint is available. |
| Institutional local endpoint (account A only) | in account A's local `.env` | `deepseek-flash`, `glm-flash`, `Qwen` | Free, reachable only from the institution's network, personal key. Runs the development judge and, under final-panel option 1, the whole panel. |

Who needs what:

- **Account A:** the local endpoint key, for all judging. A paid key only for Gate 1 (a few dollars), and for the final panel if option 2 is chosen (PLAN section 10).
- **Account B:** no judge key. Account B only runs reviews.

Check the provider's model list for the exact model ids before the first run.

If you use an Anthropic Console key for the judge, store it only as `MARTIAN_API_KEY` in `.env`, never as `ANTHROPIC_API_KEY` (see step 2).

Judge cost is measured in the pilot. Set a spending limit at the provider before the first run.

## 6. Permission to run the benchmark pipeline

Claude Code's auto mode can block running code from an external repository. The repo's `.claude/settings.json` already allows exactly these commands, so nothing is needed on either machine:

- `uv sync`: installs the benchmark's Python dependencies.
- `uv run python -m code_review_benchmark.*`: runs the benchmark's pipeline steps.

Any other command still asks for approval. Claude Code applies project settings only after you trust the folder.

After installing uv with winget, open a new terminal so `uv` is on `PATH`.

## 7. Benchmark source

The Martian benchmark is pinned in [`eval/datasets/martian/SOURCE.md`](../eval/datasets/martian/SOURCE.md). Every run uses that commit.

## Status

| Step | Account A (Pro) | Account B (Team Standard) |
|---|---|---|
| 1. Pinned version, clean setup | ✅ 2.1.317, auto-update off, clean folder logged in (Pro) | ✅ 2.1.317, auto-update off, clean folder logged in (Team) |
| 2. `ANTHROPIC_API_KEY` not set | ✅ | ✅ |
| 3. Python ≥ 3.11 and uv | ✅ Python 3.14.5, uv 0.13.7 | ✅ Python 3.14.10, uv 0.13.7 |
| 4. Git line endings | handled by the harness | handled by the harness |
| 5. Judge key in `.env` | ✅ local endpoint, all 3 models tested | not needed |
| 6. Pipeline permission | ✅ (project rule, shared) | ✅ (comes with the repo) |
