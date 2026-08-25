from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _ensure_engine():
    global _engine, _session_factory
    settings = get_settings()
    if _engine is None:
        database_url = settings.effective_database_url
        if not database_url:
            raise RuntimeError("RUNTIME_DATABASE_URL/DATABASE_URL is not configured")
        connect_args: dict[str, object] = {}
        if database_url.startswith("postgresql+asyncpg://"):
            # pool_timeout only bounds waiting for a free pooled connection;
            # it does not bound DNS/TCP/SQL time. Keep independent evals and
            # readiness probes from hanging when a managed database is asleep
            # or its hostname is temporarily unavailable.
            connect_args = {
                "timeout": settings.db_connect_timeout,
                "command_timeout": settings.db_connect_timeout,
            }
        _engine = create_async_engine(
            database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout,
            pool_recycle=settings.db_pool_recycle,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    _ensure_engine()
    assert _session_factory is not None
    async with _session_factory() as session:
        yield session


async def get_db() -> AsyncIterator[AsyncSession]:
    async with session_scope() as session:
        yield session


async def check_database() -> bool:
    try:
        settings = get_settings()
        parsed = urlparse(settings.effective_database_url)
        if parsed.hostname:
            # Resolve before constructing an asyncpg engine. This avoids
            # leaving a half-open transport behind when managed-DNS is down.
            await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(
                    parsed.hostname, parsed.port or 5432, type=socket.SOCK_STREAM
                ),
                timeout=min(2, settings.db_connect_timeout),
            )
        engine = _ensure_engine()
        async with asyncio.timeout(5):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
    finally:
        # A failed DNS/TCP attempt can leave an asyncpg transport pending even
        # after the readiness probe returns. Dispose it before the evaluator
        # exits so a blocked dependency cannot keep the process alive.
        await dispose_database()


async def dispose_database() -> None:
    global _engine, _session_factory
    if _engine is not None:
        try:
            await asyncio.wait_for(_engine.dispose(), timeout=2)
        except Exception:
            # A cancelled DNS/TCP attempt must not hold the request open while
            # the pool is being torn down. The process can safely discard the
            # engine and recreate it on the next request.
            pass
    _engine = None
    _session_factory = None
