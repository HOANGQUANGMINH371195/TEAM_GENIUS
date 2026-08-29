"""Run queued VBPL jobs outside API request processes.

Usage: ``python -m src.workers.vbpl_worker``.
The worker claims one queued job at a time; PostgreSQL lease columns allow a
future multi-worker claim implementation without changing job records.
"""
from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy import text

from src.db.session import dispose_database, session_scope
from src.services.vbpl_jobs import VbplJobService
from src.services.vbpl_sync import VbplSyncService

logger = logging.getLogger(__name__)


async def queue_daily_sync() -> str:
    """Run daily source discovery before processing queued imports."""
    import uuid

    refresh_id = uuid.uuid4().hex
    await VbplSyncService.sync_latest(refresh_id)
    return refresh_id


async def claim_job() -> str | None:
    """Return one work candidate; process_job performs atomic lease claim."""
    async with session_scope() as session:
        result = await session.execute(
            text("""
            SELECT job_id
            FROM vbpl_ingest_jobs
            WHERE status = 'queued'
               OR (status = 'running' AND lease_until IS NOT NULL AND lease_until < now())
            ORDER BY created_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """)
        )
        job_id = result.scalar_one_or_none()
        await session.commit()
        return str(job_id) if job_id else None


async def run_once() -> bool:
    job_id = await claim_job()
    if job_id:
        # The worker query only selects a candidate. The service owns the
        # atomic lease update, preventing API/worker double processing.
        await VbplJobService.process_job(job_id)
        return True
    return False


async def drain_queue() -> int:
    """Process all currently claimable jobs for one-shot cron runs."""
    processed = 0
    while await run_once():
        processed += 1
    return processed


async def run_once_or_drain() -> None:
    if os.getenv("VBPL_WORKER_DRAIN", "false").casefold() == "true":
        await drain_queue()
    else:
        await run_once()


async def run_forever() -> None:
    interval = max(1.0, float(os.getenv("VBPL_WORKER_POLL_SECONDS", "5")))
    while True:
        try:
            processed = await run_once()
            if not processed:
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("VBPL worker iteration failed")
            await asyncio.sleep(interval)


async def main() -> None:
    try:
        if os.getenv("VBPL_DAILY_SYNC", "false").casefold() == "true":
            await queue_daily_sync()
        if os.getenv("VBPL_WORKER_ONCE", "false").casefold() == "true":
            await run_once_or_drain()
        else:
            await run_forever()
    finally:
        await dispose_database()


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    asyncio.run(main())
