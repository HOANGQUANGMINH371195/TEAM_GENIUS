from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from src.config import Settings
from src.db import session as db_session


def test_effective_database_url_prefers_runtime_url():
    settings = Settings(
        database_url="postgresql+asyncpg://database",
        runtime_database_url="postgresql+asyncpg://runtime",
    )
    assert settings.effective_database_url == "postgresql+asyncpg://runtime"


def test_database_url_requires_asyncpg_driver(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://sync-driver")
    monkeypatch.delenv("RUNTIME_DATABASE_URL", raising=False)
    settings = Settings()
    with patch.object(db_session, "get_settings", return_value=settings):
        with pytest.raises(RuntimeError, match="postgresql\\+asyncpg"):
            db_session._database_url()


def test_supabase_auto_ssl_requires_tls():
    settings = Settings(db_ssl_mode="auto")
    with patch.object(db_session, "get_settings", return_value=settings):
        assert db_session._ssl_argument(
            "postgresql+asyncpg://user:pass@db.example.supabase.co:5432/postgres"
        ) == "require"


@pytest.mark.asyncio
async def test_session_scope_disposes_and_retries_connect_failure():
    settings = Settings(
        database_url="postgresql+asyncpg://user:pass@localhost:5432/db",
        db_connect_retries=2,
        db_retry_base_seconds=0,
        db_connect_timeout=1,
    )
    fake_session = AsyncMock()
    fake_factory = type("Factory", (), {"__call__": lambda self: fake_session})()

    fake_session.execute.side_effect = [TimeoutError(), AsyncMock()]
    with (
        patch.object(db_session, "get_settings", return_value=settings),
        patch.object(db_session, "_ensure_engine", side_effect=[None, None]),
        patch.object(db_session, "_session_factory", fake_factory),
        patch.object(db_session, "dispose_database", new_callable=AsyncMock) as dispose,
    ):
        async with db_session.session_scope() as session:
            assert session is fake_session

    dispose.assert_awaited_once()
    assert fake_session.close.await_count == 2


@pytest.mark.asyncio
async def test_session_scope_rolls_back_caller_failure():
    settings = Settings(
        database_url="postgresql+asyncpg://user:pass@localhost:5432/db",
        db_connect_retries=1,
        db_connect_timeout=1,
    )
    fake_session = AsyncMock()
    fake_session.execute.return_value = AsyncMock()
    fake_factory = type("Factory", (), {"__call__": lambda self: fake_session})()

    with (
        patch.object(db_session, "get_settings", return_value=settings),
        patch.object(db_session, "_ensure_engine", return_value=None),
        patch.object(db_session, "_session_factory", fake_factory),
    ):
        with pytest.raises(ValueError):
            async with db_session.session_scope():
                raise ValueError("write failed")

    fake_session.rollback.assert_awaited_once()
    fake_session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_session_scope_reuses_pool_without_dns_or_duplicate_probe(monkeypatch):
    settings = Settings(
        database_url="postgresql+asyncpg://user:pass@localhost:5432/db",
        db_connect_retries=1,
        db_connect_timeout=1,
    )
    fake_session = AsyncMock()
    fake_factory = type("Factory", (), {"__call__": lambda self: fake_session})()
    monkeypatch.setattr(db_session, "_engine", object())
    monkeypatch.setattr(db_session, "_engine_host", "127.0.0.1")
    monkeypatch.setattr(db_session, "_session_factory", fake_factory)

    with (
        patch.object(db_session, "get_settings", return_value=settings),
        patch.object(db_session, "_resolve_ipv4_hosts", new_callable=AsyncMock) as resolve,
    ):
        async with db_session.session_scope() as session:
            assert session is fake_session

    resolve.assert_not_awaited()
    fake_session.execute.assert_not_awaited()
    fake_session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_readiness_probe_keeps_shared_pool_alive():
    fake_session = AsyncMock()

    @asynccontextmanager
    async def fake_scope():
        yield fake_session

    with (
        patch.object(db_session, "session_scope", fake_scope),
        patch.object(db_session, "dispose_database", new_callable=AsyncMock) as dispose,
    ):
        assert await db_session.check_database()

    fake_session.execute.assert_awaited_once()
    dispose.assert_not_awaited()
