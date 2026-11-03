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


class EndpointBusy(BaseException):
    """A call failed for a reason outside the request: HTTP 429 (the cluster throttles when its
    queue is long) or a lost connection (VPN). A BaseException, so it passes through Martian's
    `except Exception` retry loops; JudgeModel pauses and then repeats the call itself."""


class EndpointUnavailable(Exception):
    """The endpoint stayed busy or unreachable for longer than the allowed wait."""


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

    def __init__(self, model: str, concurrency: int = 1, rate: float = 10.0, max_wait: float = 3 * 3600,
                 call_timeout: float = 180,
                 cache: Cache | None = None, log=print):
        """`rate` is the most calls started per minute; `max_wait` is the longest total pause, in
        seconds, before giving up on a busy or unreachable endpoint."""
        load_env()
        with redirect_stdout(StringIO()):  # Martian's constructors print the endpoint
            self.extractor = m2.CandidateExtractor()
            self.deduper = m25.DedupLLM()
            self.judge = m3.LLMJudge()
        for client in (self.extractor, self.deduper, self.judge):
            client.model = model
            # Martian's functions do their own retries; the SDK's hidden ones would only spend quota.
            client.client = client.client.with_options(max_retries=0)
            self._flag_busy(client.client)
        self.model = model
        self.cache = cache or Cache()
        self.sem = asyncio.Semaphore(concurrency)
        self.fingerprint = "unset"
        self.served = {}
        self.calls = 0
        self.interval = 60.0 / rate
        self.max_wait = max_wait
        # Martian waits 30 s for a dedup or judge answer. The shared endpoint can be slower for long
        # prompts (dedup of a board's ~30 candidates timed out every time), so allow 3 minutes.
        # This is the client's patience, not the method: prompts and parsing are unchanged.
        m25.LLM_CALL_TIMEOUT = m3.LLM_CALL_TIMEOUT = call_timeout
        self.backoff_base = 60.0
        self.log = log
        self._lock = asyncio.Lock()
        self._next = 0.0
        self._paused_until = 0.0
        self._backoff = 0
        self._waited = 0.0

    def _flag_busy(self, client) -> None:
        create = client.chat.completions.create

        async def flagged(*args, **kwargs):
            try:
                return await create(*args, **kwargs)
            except (openai.RateLimitError, openai.APIConnectionError) as e:
                raise EndpointBusy(f"{type(e).__name__}: {e}") from None

        client.chat.completions.create = flagged

    async def _slot(self) -> None:
        """Wait for the next call slot: at most `rate` calls a minute, none during a pause."""
        async with self._lock:
            now = time.monotonic()
            start = max(now, self._next, self._paused_until)
            self._next = start + self.interval
        await asyncio.sleep(start - now)

    def _pause(self, reason: str) -> None:
        wait = min(self.backoff_base * 2**self._backoff, 15 * self.backoff_base)
        self._backoff += 1
        self._waited += wait
        if self._waited > self.max_wait:
            raise EndpointUnavailable(f"busy or unreachable for over {self.max_wait / 60:.0f} min; last: {reason}")
        self._paused_until = max(self._paused_until, time.monotonic() + wait)
        self.log(f"{time.strftime('%H:%M:%S')} endpoint busy or unreachable, pausing {wait:.0f} s ({reason[:120]})")

    def _ok(self) -> None:
        self._backoff = 0
        self._waited = 0.0

    async def _call(self, call):
        """One paced call; on EndpointBusy, pause and repeat it."""
        while True:
            await self._slot()
            self.calls += 1
            try:
                result = await call()
            except EndpointBusy as e:
                self._pause(str(e))
                continue
            self._ok()
            return result

    async def identify(self) -> dict:
        """Record the endpoint's model-list entry for this model (PLAN 5.1: log the version).

        The fingerprint is part of every cache key, so a changed entry means re-judging.
        The local endpoint's entries carry no version, so they can't reveal a silent model
        update; J3's repeat check covers that.
        """
        async def listing():
            try:
                return await self.judge.client.models.list()
            except (openai.RateLimitError, openai.APIConnectionError) as e:
                raise EndpointBusy(f"{type(e).__name__}: {e}") from None

        page = await self._call(listing)
        entry = next((m.model_dump() for m in page.data if m.id == self.model), None)
        if entry is None:
            raise LLMError(f"{self.model} is not in the endpoint's model list")
        self.served = entry
        self.fingerprint = _sha(json.dumps(entry, sort_keys=True))[:12]
        return entry

    async def _cached(self, step: str, prompt: str, call):
        key = _sha(step, self.model, self.fingerprint, prompt)
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        async with self.sem:
            result = await self._call(call)
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
