"""Bounded asynchronous research-job orchestration.

Deep/global requests must not hold an interactive SSE request open
indefinitely.  ``ResearchJobQueue`` is an explicit bounded worker contract for
local/staging and can be backed by a durable queue at deployment time.  It
enforces owner/conversation isolation, deadline cancellation and a small
concurrency limit; it does not make legal decisions or persist evidence.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

JobStatus = Literal["pending", "running", "completed", "failed", "cancelled", "expired"]
JobExecutor = Callable[["ResearchJob"], Awaitable[dict[str, object]]]


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class ResearchJob:
    job_id: str
    owner_uid: str
    conversation_id: str
    release_id: str
    query: str
    status: JobStatus = "pending"
    result: dict[str, object] | None = None
    error: str = ""
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    expires_at: datetime = field(default_factory=lambda: _now() + timedelta(minutes=15))

    def public_status(self) -> dict[str, object]:
        """Return status metadata without exposing internal evidence/query text."""
        return {
            "job_id": self.job_id,
            "status": self.status,
            "release_id": self.release_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "error": self.error,
            "result": self.result,
        }


class ResearchQueueFullError(RuntimeError):
    """Raised when the bounded queue cannot accept another job."""


class ResearchJobQueue:
    """Run bounded, owner-isolated research jobs in the current process."""

    def __init__(
        self,
        *,
        max_pending: int = 32,
        max_workers: int = 2,
        timeout_seconds: float = 90.0,
        ttl_seconds: int = 900,
    ) -> None:
        if min(max_pending, max_workers, ttl_seconds) < 1 or timeout_seconds <= 0:
            raise ValueError("queue limits must be positive")
        self.max_pending = max_pending
        self.timeout_seconds = timeout_seconds
        self.ttl_seconds = ttl_seconds
        self._semaphore = asyncio.Semaphore(max_workers)
        self._jobs: dict[str, ResearchJob] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    async def submit(
        self,
        *,
        owner_uid: str,
        conversation_id: str,
        release_id: str,
        query: str,
        executor: JobExecutor,
    ) -> ResearchJob:
        """Enqueue one job and start it under the worker semaphore."""
        if not owner_uid.strip() or not conversation_id.strip() or not release_id.strip():
            raise ValueError("owner_uid, conversation_id and release_id are required")
        normalized_query = " ".join(query.split())
        if not normalized_query:
            raise ValueError("query must not be blank")
        async with self._lock:
            if self._closed:
                raise RuntimeError("research queue is closed")
            active = sum(job.status in {"pending", "running"} for job in self._jobs.values())
            if active >= self.max_pending:
                raise ResearchQueueFullError("research queue is full")
            now = _now()
            job = ResearchJob(
                job_id=uuid4().hex,
                owner_uid=owner_uid,
                conversation_id=conversation_id,
                release_id=release_id,
                query=normalized_query,
                created_at=now,
                updated_at=now,
                expires_at=now + timedelta(seconds=self.ttl_seconds),
            )
            self._jobs[job.job_id] = job
            self._tasks[job.job_id] = asyncio.create_task(self._run(job, executor))
            return job

    async def _run(self, job: ResearchJob, executor: JobExecutor) -> None:
        async with self._semaphore:
            if job.status == "cancelled":
                return
            job.status = "running"
            job.updated_at = _now()
            try:
                result = await asyncio.wait_for(executor(job), timeout=self.timeout_seconds)
            except asyncio.CancelledError:
                job.status = "cancelled"
                job.error = "cancelled"
                raise
            except TimeoutError:
                job.status = "expired"
                job.error = "research deadline exceeded"
            except Exception as exc:  # worker boundary records, never leaks details
                job.status = "failed"
                job.error = type(exc).__name__
            else:
                job.status = "completed"
                job.result = dict(result)
            finally:
                job.updated_at = _now()

    async def get(
        self, *, owner_uid: str, conversation_id: str, job_id: str
    ) -> ResearchJob | None:
        """Look up a job only within its owner/conversation namespace."""
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.owner_uid != owner_uid or job.conversation_id != conversation_id:
                return None
            if _now() >= job.expires_at and job.status in {"pending", "running"}:
                job.status = "expired"
                job.error = "job retention expired"
                job.updated_at = _now()
            return job

    async def cancel(self, *, owner_uid: str, conversation_id: str, job_id: str) -> bool:
        job = await self.get(owner_uid=owner_uid, conversation_id=conversation_id, job_id=job_id)
        if job is None or job.status not in {"pending", "running"}:
            return False
        job.status = "cancelled"
        job.error = "cancelled by owner"
        job.updated_at = _now()
        task = self._tasks.get(job_id)
        if task is not None and not task.done():
            task.cancel()
        return True

    async def close(self) -> None:
        """Cancel workers during graceful shutdown and prevent new jobs."""
        async with self._lock:
            self._closed = True
            tasks = list(self._tasks.values())
            for job in self._jobs.values():
                if job.status in {"pending", "running"}:
                    job.status = "cancelled"
                    job.error = "queue shutdown"
                    job.updated_at = _now()
            for task in tasks:
                if not task.done():
                    task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
