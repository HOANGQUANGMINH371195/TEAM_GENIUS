"""Private, bounded conversation navigation cache.

The cache is an optimization only: every legal turn still re-retrieves the
active release. Redis is used when configured; a bounded in-process fallback
keeps local development functional.
"""

from __future__ import annotations

import hashlib
import json
import time
import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from src.config import get_settings
from src.services.metrics import metrics


class ConversationContextCache:
    def __init__(self, *, url: str = "", ttl_seconds: int = 120, max_turns: int = 10) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_turns = max_turns
        self._memory: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._redis = None
        if url:
            try:
                import redis.asyncio as redis

                self._redis = redis.from_url(url, decode_responses=True)
            except Exception:
                self._redis = None

    @staticmethod
    def _key(owner_uid: str, conversation_id: str) -> str:
        digest = hashlib.sha256(f"{owner_uid}\x00{conversation_id}".encode()).hexdigest()
        return f"medipay:conversation-context:{digest}"

    async def get(self, *, owner_uid: str, conversation_id: str) -> list[dict[str, Any]] | None:
        key = self._key(owner_uid, conversation_id)
        if self._redis is not None:
            try:
                raw = await self._redis.get(key)
                metrics.inc("conversation_cache_requests_total", outcome="hit" if raw else "miss", backend="redis")
                return json.loads(raw) if raw else None
            except Exception:
                metrics.inc("conversation_cache_requests_total", outcome="error", backend="redis")
        cached = self._memory.get(key)
        if not cached or time.monotonic() - cached[0] >= self.ttl_seconds:
            self._memory.pop(key, None)
            metrics.inc("conversation_cache_requests_total", outcome="miss", backend="memory")
            return None
        metrics.inc("conversation_cache_requests_total", outcome="hit", backend="memory")
        return [dict(item) for item in cached[1]]

    async def put(self, *, owner_uid: str, conversation_id: str, turns: list[dict[str, Any]]) -> None:
        key = self._key(owner_uid, conversation_id)
        bounded = [dict(item) for item in turns[-self.max_turns :]]
        if self._redis is not None:
            try:
                await self._redis.set(key, json.dumps(bounded, ensure_ascii=False, default=str), ex=self.ttl_seconds)
                return
            except Exception:
                pass
        if len(self._memory) >= 512:
            self._memory.pop(next(iter(self._memory)), None)
        self._memory[key] = (time.monotonic(), bounded)

    async def get_or_load(
        self,
        *,
        owner_uid: str,
        conversation_id: str,
        loader: Callable[[], Awaitable[list[dict[str, Any]]]],
    ) -> list[dict[str, Any]]:
        """Single-flight a cache miss so a new conversation cannot stampede SQL."""
        key = self._key(owner_uid, conversation_id)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = await self.get(owner_uid=owner_uid, conversation_id=conversation_id)
            if cached is not None:
                return cached
            turns = await loader()
            await self.put(owner_uid=owner_uid, conversation_id=conversation_id, turns=turns)
            return [dict(item) for item in turns[-self.max_turns :]]

    async def invalidate(self, *, owner_uid: str, conversation_id: str) -> None:
        key = self._key(owner_uid, conversation_id)
        self._memory.pop(key, None)
        self._locks.pop(key, None)
        if self._redis is not None:
            try:
                await self._redis.delete(key)
            except Exception:
                pass

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()


_cache: ConversationContextCache | None = None


def get_conversation_cache() -> ConversationContextCache:
    global _cache
    if _cache is None:
        settings = get_settings()
        _cache = ConversationContextCache(
            url=settings.rate_limit_redis_url,
            ttl_seconds=settings.conversation_cache_ttl_seconds,
            max_turns=settings.conversation_cache_max_turns,
        )
    return _cache
