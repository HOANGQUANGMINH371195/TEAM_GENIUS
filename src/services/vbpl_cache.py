"""Short-lived Redis cache and distributed locks for VBPL operations."""
from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from src.config import get_settings

DISCOVERY_KEY = "medipay:vbpl:discovery:latest"
SESSION_KEY = "medipay:vbpl:session"
SYNC_KEY_PREFIX = "medipay:vbpl:sync:"
LOCK_KEY_PREFIX = "medipay:vbpl:lock:"

# Development fallback only. Production should configure RATE_LIMIT_REDIS_URL.
_memory: dict[str, tuple[float, str]] = {}


class VbplCache:
    """Redis-backed JSON cache with bounded in-process fallback."""

    @classmethod
    def configured(cls) -> bool:
        return bool(get_settings().rate_limit_redis_url.strip())

    @staticmethod
    def _url() -> str:
        return get_settings().rate_limit_redis_url.strip()

    @classmethod
    async def _client(cls):
        url = cls._url()
        if not url:
            return None
        import redis.asyncio as redis

        return redis.from_url(url, decode_responses=True)

    @classmethod
    async def get_json(cls, key: str) -> dict[str, Any] | None:
        client = await cls._client()
        if client is not None:
            try:
                value = await client.get(key)
                return json.loads(value) if value else None
            except Exception:
                # Redis is an accelerator, not source of truth. Continue with
                # local fallback when Redis is unavailable in development.
                return None
            finally:
                await client.aclose()
        entry = _memory.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= time.monotonic():
            _memory.pop(key, None)
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            _memory.pop(key, None)
            return None

    @classmethod
    async def set_json(cls, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        ttl = max(1, int(ttl_seconds))
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        client = await cls._client()
        if client is not None:
            try:
                await client.setex(key, ttl, encoded)
            except Exception:
                # Cache failures must not take down discovery in development.
                pass
            finally:
                await client.aclose()
            return
        _memory[key] = (time.monotonic() + ttl, encoded)

    @classmethod
    async def delete(cls, key: str) -> None:
        client = await cls._client()
        if client is not None:
            try:
                await client.delete(key)
            finally:
                await client.aclose()
            return
        _memory.pop(key, None)

    @classmethod
    async def acquire_lock(cls, name: str, ttl_seconds: int) -> str | None:
        token = uuid.uuid4().hex
        key = LOCK_KEY_PREFIX + name
        client = await cls._client()
        if client is not None:
            try:
                acquired = await client.set(key, token, nx=True, ex=max(1, int(ttl_seconds)))
                return token if acquired else None
            except Exception:
                return None
            finally:
                await client.aclose()
        now = time.monotonic()
        current = _memory.get(key)
        if current is not None and current[0] > now:
            return None
        _memory[key] = (now + max(1, int(ttl_seconds)), token)
        return token

    @classmethod
    async def release_lock(cls, name: str, token: str) -> None:
        key = LOCK_KEY_PREFIX + name
        client = await cls._client()
        if client is not None:
            try:
                # Delete only lock owned by this caller.
                await client.eval(
                    "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
                    1,
                    key,
                    token,
                )
            except Exception:
                pass
            finally:
                await client.aclose()
            return
        current = _memory.get(key)
        if current and current[1] == token:
            _memory.pop(key, None)

    @classmethod
    @asynccontextmanager
    async def lock(cls, name: str, ttl_seconds: int) -> AsyncIterator[bool]:
        token = await cls.acquire_lock(name, ttl_seconds)
        try:
            yield token is not None
        finally:
            if token is not None:
                await cls.release_lock(name, token)

    @classmethod
    async def get_discovery(cls) -> dict[str, Any] | None:
        return await cls.get_json(DISCOVERY_KEY)

    @classmethod
    async def set_discovery(cls, value: dict[str, Any]) -> None:
        settings = get_settings()
        await cls.set_json(DISCOVERY_KEY, value, settings.vbpl_discovery_ttl_seconds)

    @classmethod
    async def get_session(cls) -> dict[str, Any] | None:
        return await cls.get_json(SESSION_KEY)

    @classmethod
    async def set_session(cls, value: dict[str, Any]) -> None:
        settings = get_settings()
        await cls.set_json(SESSION_KEY, value, settings.vbpl_session_ttl_seconds)

    @classmethod
    async def set_sync(cls, refresh_id: str, value: dict[str, Any]) -> None:
        settings = get_settings()
        await cls.set_json(SYNC_KEY_PREFIX + refresh_id, value, settings.vbpl_sync_ttl_seconds)

    @classmethod
    async def get_sync(cls, refresh_id: str) -> dict[str, Any] | None:
        return await cls.get_json(SYNC_KEY_PREFIX + refresh_id)
