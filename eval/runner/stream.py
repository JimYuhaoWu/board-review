"""Reading a `claude -p --output-format stream-json --verbose` run: the init event, the result
event, usage-limit stops, the setup check, and a tolerant parse of the board's final JSON."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel

_USAGE_LIMIT = re.compile(r"usage limit|limit reached|rate limit|resets? (at|in)|too many requests", re.I)
_FENCE = re.compile(r"^```[a-zA-Z]*\s*\n?|\n?```\s*$")


class InitInfo(BaseModel):
    """What the run loaded, from the `system`/`init` event."""

    version: str
    model: str
    tools: list[str]
    mcp_servers: list[str]
    plugins: list[str]
    skills: list[str]

    @classmethod
    def from_event(cls, event: dict[str, Any]) -> InitInfo:
        return cls(
            version=event.get("claude_code_version", ""),
            model=event.get("model", ""),
            tools=list(event.get("tools", [])),
            mcp_servers=[s.get("name", "") for s in event.get("mcp_servers", [])],
            plugins=[p.get("source", p.get("name", "")) for p in event.get("plugins", [])],
            skills=list(event.get("skills", [])),
        )


class ResultInfo(BaseModel):
    """The final `result` event."""

    is_error: bool
    subtype: str
    text: str
    total_cost_usd: float | None = None
    # The top-level `usage` counts only the main thread; work done in agents shows up in `modelUsage`.
    model_usage: dict[str, Any] = {}
    subagents: dict[str, Any] = {}
    duration_ms: int | None = None
    num_turns: int | None = None
    permission_denials: int = 0
    terminal_reason: str | None = None
    api_error_status: int | None = None

    @classmethod
    def from_event(cls, event: dict[str, Any]) -> ResultInfo:
        return cls(
            is_error=bool(event.get("is_error")),
            subtype=event.get("subtype", ""),
            text=event.get("result") or "",
            total_cost_usd=event.get("total_cost_usd"),
            model_usage=event.get("modelUsage") or {},
            subagents=event.get("subagent_stats") or {},
            duration_ms=event.get("duration_ms"),
            num_turns=event.get("num_turns"),
            permission_denials=len(event.get("permission_denials") or []),
            terminal_reason=event.get("terminal_reason"),
            api_error_status=event.get("api_error_status"),
        )

    @property
    def usage_limit(self) -> bool:
        # The exact shape of a plan usage-limit stop isn't documented; match the status and the text.
        if not self.is_error:
            return False
        return self.api_error_status == 429 or bool(_USAGE_LIMIT.search(f"{self.text} {self.terminal_reason or ''}"))


class ExpectedSetup(BaseModel):
    """The clean setup every benchmark run must have (PLAN Phase 0, Phase 2 step 2)."""

    version: str
    model: str
    tools: list[str]
    plugin: str = "board-review"


def setup_problems(init: InitInfo, expected: ExpectedSetup) -> list[str]:
    problems = []
    if init.version != expected.version:
        problems.append(f"Claude Code {init.version}, expected {expected.version}")
    if init.model != expected.model:
        problems.append(f"model {init.model}, expected {expected.model}")
    if sorted(init.tools) != sorted(expected.tools):
        problems.append(f"tools {sorted(init.tools)}, expected {sorted(expected.tools)}")
    if init.mcp_servers:
        problems.append(f"MCP servers loaded: {init.mcp_servers}")
    extra_plugins = [p for p in init.plugins if not p.endswith("@builtin") and not p.startswith(f"{expected.plugin}@")]
    if extra_plugins:
        problems.append(f"plugins loaded: {extra_plugins}")
    if not any(p.startswith(f"{expected.plugin}@") for p in init.plugins):
        problems.append(f"plugin {expected.plugin} not loaded")
    # Built-in skills have bare names; plugin and synced skills are namespaced.
    extra_skills = [s for s in init.skills if ":" in s and not s.startswith(f"{expected.plugin}:")]
    if extra_skills:
        problems.append(f"skills loaded: {extra_skills}")
    return problems


def parse_board_json(text: str) -> tuple[dict[str, Any], str]:
    """Parse the board's final message, repairing the known slips. Returns (object, repair)."""
    candidate = text.strip()
    repairs = []
    if candidate.startswith("```"):
        candidate = _FENCE.sub("", candidate).strip()
        repairs.append("stripped-fence")
    if not candidate.startswith("{") and candidate.startswith('"'):
        # The model once dropped the opening brace in the smoke test.
        candidate = "{" + candidate
        repairs.append("added-brace")
    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            raise
        obj = json.loads(candidate[start : end + 1])
        repairs.append("extracted-object")
    if not isinstance(obj, dict):
        raise ValueError("final message is JSON but not an object")
    return obj, "+".join(repairs) or "none"
