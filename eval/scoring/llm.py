"""Cached, rate-limited judge-model calls, made through Martian's own LLM classes.

Every call goes through the vendored Martian prompt and call function, so a cached
answer is exactly what Martian's step would have produced. Answers are cached in
eval/cache/llm.sqlite, keyed by step, model, served-model fingerprint, and prompt.
Failed calls are never cached.
"""

import asyncio
import hashlib
import json
import os
import sqlite3
import time

import openai
from contextlib import redirect_stdout
from io import StringIO

from .data import ROOT
from .martian import step2_5_dedup_candidates as m25
from .martian import step2_extract_comments as m2
from .martian import step3_judge_comments as m3

CACHE_FILE = ROOT / "eval" / "cache" / "llm.sqlite"


class LLMError(RuntimeError):
    pass


class QuotaStop(BaseException):
    """The endpoint refused a call for quota (HTTP 429). A BaseException, so it passes through
    Martian's `except Exception` retry loops: on a quota, every retry counts against it."""


def load_env() -> None:
    """Load the repo's .env (judge endpoint and key) without overriding the shell."""
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _sha(*parts: str) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


class Cache:
    def __init__(self, path=CACHE_FILE):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS calls (key TEXT PRIMARY KEY, step TEXT, model TEXT,"
            " fingerprint TEXT, response TEXT, created REAL)"
        )
        if "prompt" not in [c[1] for c in self.db.execute("PRAGMA table_info(calls)")]:
            self.db.execute("ALTER TABLE calls ADD COLUMN prompt TEXT")  # kept for audits and re-keying

    def get(self, key: str):
        row = self.db.execute("SELECT response FROM calls WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, step: str, model: str, fingerprint: str, response, prompt: str) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO calls (key, step, model, fingerprint, response, created, prompt)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (key, step, model, fingerprint, json.dumps(response, ensure_ascii=False), time.time(), prompt),
        )
        self.db.commit()


class JudgeModel:
    """One judge model on an OpenAI-compatible endpoint: extraction, dedup, and judging."""

    def __init__(self, model: str, concurrency: int = 4, cache: Cache | None = None):
        load_env()
        with redirect_stdout(StringIO()):  # Martian's constructors print the endpoint
            self.extractor = m2.CandidateExtractor()
            self.deduper = m25.DedupLLM()
            self.judge = m3.LLMJudge()
        for client in (self.extractor, self.deduper, self.judge):
            client.model = model
            # Martian's functions do their own retries; the SDK's hidden ones would only spend quota.
            client.client = client.client.with_options(max_retries=0)
            self._stop_on_quota(client.client)
        self.model = model
        self.cache = cache or Cache()
        self.sem = asyncio.Semaphore(concurrency)
        self.fingerprint = "unset"
        self.served = {}
        self.calls = 0
        self.stopped = None

    def _stop_on_quota(self, client) -> None:
        create = client.chat.completions.create

        async def guarded(*args, **kwargs):
            if self.stopped:
                raise QuotaStop(self.stopped)
            try:
                return await create(*args, **kwargs)
            except openai.RateLimitError as e:
                self.stopped = str(e)
                raise QuotaStop(self.stopped) from None

        client.chat.completions.create = guarded

    async def identify(self) -> dict:
        """Record the endpoint's model-list entry for this model (PLAN 5.1: log the version).

        The fingerprint is part of every cache key, so a changed entry means re-judging.
        The local endpoint's entries carry no version, so they can't reveal a silent model
        update; J3's repeat check covers that.
        """
        error = None
        for attempt in range(3):
            try:
                page = await self.judge.client.models.list()
                entry = next((m.model_dump() for m in page.data if m.id == self.model), None)
                if entry is None:
                    raise LLMError(f"{self.model} is not in the endpoint's model list")
                self.served = entry
                self.fingerprint = _sha(json.dumps(entry, sort_keys=True))[:12]
                return entry
            except LLMError:
                raise
            except openai.RateLimitError as e:
                self.stopped = str(e)
                raise QuotaStop(self.stopped) from None
            except Exception as e:  # noqa: BLE001 - network errors: retry, then give up
                error = e
                await asyncio.sleep(2**attempt)
        raise LLMError(f"could not list the endpoint's models: {error}")

    async def _cached(self, step: str, prompt: str, call):
        key = _sha(step, self.model, self.fingerprint, prompt)
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        async with self.sem:
            self.calls += 1
            result = await call()
        if result is None or (isinstance(result, dict) and result.get("error")):
            raise LLMError(f"{step} failed: {result.get('error') if isinstance(result, dict) else 'no result'}")
        self.cache.put(key, step, self.model, self.fingerprint, result, prompt)
        return result

    async def extract(self, body: str) -> list[str]:
        """Martian step 2 for one comment body."""
        if not body or len(body.strip()) < 20:  # Martian skips these
            return []
        prompt = m2.EXTRACT_PROMPT.format(comment=body)
        result = await self._cached("extract", prompt, lambda: self.extractor.call_llm(prompt))
        return [str(i) for i in result.get("issues", []) if i]

    async def dedup(self, texts: list[str]) -> tuple[list[list[int]] | None, bool]:
        """Martian step 2.5 for one review. Returns (groups, fell_back_to_singletons)."""
        if len(texts) < m25.MIN_CANDIDATES:
            return None, False
        numbered = "\n".join(f"{i}. {text}" for i, text in enumerate(texts))
        prompt = m25.DEDUP_PROMPT.format(candidates=numbered)

        async def call():
            with redirect_stdout(StringIO()):
                groups = await self.deduper.dedup_candidates(texts, m25.DEDUP_PROMPT)
            return {"groups": groups} if groups is not None else None

        try:
            result = await self._cached("dedup", prompt, call)
        except LLMError:
            # Martian falls back to all singletons; the record notes it, and the cache does not keep it.
            return [[i] for i in range(len(texts))], True
        return result["groups"], False

    async def match(self, golden: str, candidate: str) -> dict:
        """Martian step 3 for one (golden comment, candidate) pair."""
        prompt = m3.JUDGE_PROMPT.format(golden_comment=golden, candidate=candidate)

        async def call():
            result = await self.judge.call_llm(prompt)
            if "match" not in result and not result.get("error"):
                return {"error": f"judge answer has no 'match': {result}"}
            return result

        return await self._cached("judge", prompt, call)
