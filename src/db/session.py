from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlparse

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_engine_host: str | None = None


def _database_url() -> str:
    database_url = get_settings().effective_database_url.strip()
    if not database_url:
        raise RuntimeError("RUNTIME_DATABASE_URL/DATABASE_URL is not configured")
    if not database_url.lower().startswith("postgresql+asyncpg://"):
        raise RuntimeError("DATABASE_URL must use the postgresql+asyncpg:// driver")
    return database_url


def _ssl_argument(database_url: str) -> str | bool | None:
    """Translate env/URL SSL settings to asyncpg's SSL argument."""
    settings = get_settings()
    parsed = urlparse(database_url)
    requested = settings.db_ssl_mode
    query_mode = parse_qs(parsed.query).get("sslmode", [""])[0].lower()
    if requested == "auto" and query_mode:
        requested = query_mode if query_mode in {"require", "prefer", "disable"} else "auto"

    if requested == "require":
        return "require"
    if requested == "disable":
        return False
    if requested == "prefer":
        return None

    host = (parsed.hostname or "").lower()
    if host.endswith((".supabase.com", ".supabase.co")):
        return "require"
    return None


def _engine_url(database_url: str, host: str | None = None) -> URL:
    """Build asyncpg URL, removing libpq-only query parameters."""
    url = make_url(database_url)
    query = dict(url.query)
    query.pop("sslmode", None)
    query.pop("channel_binding", None)
    if host and host != url.host:
        url = url.set(host=host)
    return url.set(query=query)


def _connect_args(database_url: str) -> dict[str, object]:
    settings = get_settings()
    args: dict[str, object] = {
        "timeout": settings.db_connect_timeout,
        "command_timeout": settings.db_connect_timeout,
    }
    ssl = _ssl_argument(database_url)
    if ssl is not None:
        args["ssl"] = ssl
    return args


def _ensure_engine(database_url: str | None = None, host: str | None = None):
    global _engine, _session_factory, _engine_host
    settings = get_settings()
    database_url = database_url or _database_url()
    if _engine is None:
        _engine = create_async_engine(
            _engine_url(database_url, host),
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout,
            pool_recycle=settings.db_pool_recycle,
            pool_pre_ping=True,
            connect_args=_connect_args(database_url),
        )
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
        _engine_host = host
    return _engine


async def _resolve_ipv4_hosts(database_url: str) -> tuple[str, ...]:
    """Resolve reachable IPv4 candidates before opening managed pooler connections.

    Some Windows networks return a NAT64/IPv6 address first for Supabase's
    pooler hostname, while direct IPv4 endpoints are reachable. Restricting
    this resolution to IPv4 avoids hanging on an unusable first address while
    retaining hostname fallback for local or IPv6-only databases.
    """
    settings = get_settings()
    parsed = urlparse(database_url)
    if not parsed.hostname:
        return ()
    try:
        addresses = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(
                parsed.hostname,
                parsed.port or 5432,
                family=socket.AF_INET,
                type=socket.SOCK_STREAM,
            ),
            timeout=min(2.0, float(settings.db_connect_timeout)),
        )
    except (OSError, TimeoutError):
        return ()
    return tuple(dict.fromkeys(address[4][0] for address in addresses))


async def _dispose_after_connect_failure(session: AsyncSession | None) -> None:
    if session is not None:
        try:
            await session.close()
        except Exception:
            pass
    await dispose_database()


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Yield a verified session, retrying only connection acquisition.

    Caller work is never retried. A failed acquisition disposes the engine
    before the next host/attempt, recovering stale managed pools without
    replaying a write transaction.
    """
    settings = get_settings()
    database_url = _database_url()
    retries = settings.db_connect_retries
    # Resolve managed-DB hosts only when constructing/reconstructing the pool.
    # Doing DNS work before every short repository session multiplied latency
    # across the retrieval DAG even though all sessions shared one engine.
    if _engine is None:
        resolved_hosts = await _resolve_ipv4_hosts(database_url)
        candidates: tuple[str | None, ...] = resolved_hosts or (None,)
    else:
        candidates = (_engine_host,)

    for attempt in range(retries):
        session: AsyncSession | None = None
        # Keep an existing healthy pool. A failed attempt disposes it and the
        # next attempt rotates to another resolved IPv4 candidate.
        host = _engine_host if _engine is not None else candidates[attempt % len(candidates)]
        verify_new_pool = _engine is None
        try:
            _ensure_engine(database_url, host)
            assert _session_factory is not None
            session = _session_factory()
            # ``pool_pre_ping`` validates every physical checkout. The explicit
            # probe is needed only once when a pool is first created so host
            # rotation/retry remains bounded without adding an extra SQL RTT to
            # every repository operation.
            if verify_new_pool:
                async with asyncio.timeout(settings.db_connect_timeout):
                    await session.execute(text("SELECT 1"))
        except Exception:
            await _dispose_after_connect_failure(session)
            if attempt + 1 < retries:
                delay = settings.db_retry_base_seconds * (2**attempt)
                if delay > 0:
                    await asyncio.sleep(delay)
                continue
            raise

        try:
            yield session
        except BaseException:
            try:
                await session.rollback()
            finally:
                await session.close()
            raise
        else:
            await session.close()
        return

    raise RuntimeError("Database session acquisition failed")  # pragma: no cover


async def get_db() -> AsyncIterator[AsyncSession]:
    async with session_scope() as session:
        yield session


async def check_database() -> bool:
    try:
        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def dispose_database() -> None:
    global _engine, _session_factory, _engine_host
    if _engine is not None:
        try:
            await asyncio.wait_for(_engine.dispose(), timeout=2)
        except Exception:
            pass
    _engine = None
    _session_factory = None
    _engine_host = None
