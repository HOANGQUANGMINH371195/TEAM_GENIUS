"""Bounded asynchronous research-job orchestration.

Deep/global requests must not hold an interactive SSE request open
indefinitely.  ``ResearchJobQueue`` is an explicit bounded worker contract for
local/staging and can be backed by a durable queue at deployment time.  It
enforces owner/conversation isolation, deadline cancellation and a small
concurrency limit; it does not make legal decisions or persist evidence.
"""

from __future__ import annotations

import asyncio
import json
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


class RedisResearchJobQueue:
    """Durable owner-isolated queue for deep research jobs.

    Redis is used only for job metadata and a FIFO pending list.  Workers may
    run in separate Render instances; ``BLPOP`` assigns each job to one worker
    and the status record makes retries/cancellation observable.  The
    executor is deliberately injected, so this class cannot silently turn a
    queue item into an ungrounded legal answer.
    """

    def __init__(
        self,
        *,
        url: str = "",
        redis_client: object | None = None,
        max_pending: int = 32,
        timeout_seconds: float = 90.0,
        ttl_seconds: int = 900,
        namespace: str = "medipay:research",
    ) -> None:
        if min(max_pending, ttl_seconds) < 1 or timeout_seconds <= 0:
            raise ValueError("queue limits must be positive")
        self.max_pending = max_pending
        self.timeout_seconds = timeout_seconds
        self.ttl_seconds = ttl_seconds
        self.namespace = namespace.rstrip(":")
        self._redis = redis_client
        if self._redis is None and url:
            try:
                import redis.asyncio as redis

                self._redis = redis.from_url(url, decode_responses=True)
            except Exception as exc:  # pragma: no cover - optional runtime import
                raise RuntimeError("redis package is required for the redis queue") from exc
        if self._redis is None:
            raise ValueError("redis_client or url is required")
        self._closed = False

    @property
    def pending_key(self) -> str:
        return f"{self.namespace}:pending"

    @property
    def active_key(self) -> str:
        return f"{self.namespace}:active"

    def _job_key(self, job_id: str) -> str:
        return f"{self.namespace}:job:{job_id}"

    @staticmethod
    def _encode(job: ResearchJob) -> str:
        return json.dumps({
            "job_id": job.job_id,
            "owner_uid": job.owner_uid,
            "conversation_id": job.conversation_id,
            "release_id": job.release_id,
            "query": job.query,
            "status": job.status,
            "result": job.result,
            "error": job.error,
            "created_at": job.created_at.isoformat(),
            "updated_at": job.updated_at.isoformat(),
            "expires_at": job.expires_at.isoformat(),
        }, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _decode(raw: str | bytes) -> ResearchJob:
        value = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        return ResearchJob(
            job_id=str(value["job_id"]), owner_uid=str(value["owner_uid"]),
            conversation_id=str(value["conversation_id"]), release_id=str(value["release_id"]),
            query=str(value["query"]), status=value["status"], result=value.get("result"),
            error=str(value.get("error") or ""),
            created_at=datetime.fromisoformat(value["created_at"]),
            updated_at=datetime.fromisoformat(value["updated_at"]),
            expires_at=datetime.fromisoformat(value["expires_at"]),
        )

    async def _save(self, job: ResearchJob) -> None:
        await self._redis.set(self._job_key(job.job_id), self._encode(job), ex=self.ttl_seconds)

    async def submit(
        self,
        *,
        owner_uid: str,
        conversation_id: str,
        release_id: str,
        query: str,
    ) -> ResearchJob:
        if self._closed:
            raise RuntimeError("research queue is closed")
        if not owner_uid.strip() or not conversation_id.strip() or not release_id.strip():
            raise ValueError("owner_uid, conversation_id and release_id are required")
        normalized_query = " ".join(query.split())
        if not normalized_query:
            raise ValueError("query must not be blank")
        job_id = uuid4().hex
        now = _now()
        job = ResearchJob(
            job_id=job_id, owner_uid=owner_uid, conversation_id=conversation_id,
            release_id=release_id, query=normalized_query, created_at=now,
            updated_at=now, expires_at=now + timedelta(seconds=self.ttl_seconds),
        )
        added = await self._redis.sadd(self.active_key, job_id)
        if not added or int(await self._redis.scard(self.active_key)) > self.max_pending:
            await self._redis.srem(self.active_key, job_id)
            raise ResearchQueueFullError("research queue is full")
        await self._save(job)
        await self._redis.rpush(self.pending_key, job_id)
        return job

    async def get(self, *, owner_uid: str, conversation_id: str, job_id: str) -> ResearchJob | None:
        raw = await self._redis.get(self._job_key(job_id))
        if raw is None:
            return None
        job = self._decode(raw)
        if job.owner_uid != owner_uid or job.conversation_id != conversation_id:
            return None
        if _now() >= job.expires_at and job.status in {"pending", "running"}:
            job.status = "expired"
            job.error = "job retention expired"
            job.updated_at = _now()
            await self._save(job)
            await self._redis.srem(self.active_key, job.job_id)
        return job

    async def cancel(self, *, owner_uid: str, conversation_id: str, job_id: str) -> bool:
        job = await self.get(owner_uid=owner_uid, conversation_id=conversation_id, job_id=job_id)
        if job is None or job.status not in {"pending", "running"}:
            return False
        job.status = "cancelled"
        job.error = "cancelled by owner"
        job.updated_at = _now()
        await self._save(job)
        await self._redis.srem(self.active_key, job.job_id)
        return True

    async def run_once(self, executor: JobExecutor, *, block_seconds: int = 1) -> ResearchJob | None:
        """Claim and execute at most one job; safe for a multi-process worker."""
        if self._closed:
            return None
        item = await self._redis.blpop(self.pending_key, timeout=max(0, int(block_seconds)))
        if not item:
            return None
        _, job_id = item
        raw = await self._redis.get(self._job_key(job_id))
        if raw is None:
            await self._redis.srem(self.active_key, job_id)
            return None
        job = self._decode(raw)
        if job.status != "pending" or _now() >= job.expires_at:
            job.status = "cancelled" if job.status == "cancelled" else "expired"
            job.error = job.error or "job expired before worker claim"
            job.updated_at = _now()
            await self._save(job)
            await self._redis.srem(self.active_key, job_id)
            return job
        job.status = "running"
        job.updated_at = _now()
        await self._save(job)
        try:
            result = await asyncio.wait_for(executor(job), timeout=self.timeout_seconds)
        except asyncio.CancelledError:
            job.status, job.error = "cancelled", "cancelled"
            raise
        except TimeoutError:
            job.status, job.error = "expired", "research deadline exceeded"
        except Exception as exc:  # worker boundary must not leak details
            job.status, job.error = "failed", type(exc).__name__
        else:
            job.status, job.result = "completed", dict(result)
        finally:
            job.updated_at = _now()
            await self._save(job)
            await self._redis.srem(self.active_key, job_id)
        return job

    async def close(self) -> None:
        self._closed = True
        close = getattr(self._redis, "aclose", None)
        if close is not None:
            await close()


def create_research_queue(*, settings: object | None = None) -> ResearchJobQueue | RedisResearchJobQueue:
    """Create the configured queue without silently downgrading durability."""
    if settings is None:
        from src.config import get_settings

        settings = get_settings()
    backend = str(getattr(settings, "research_queue_backend", "memory")).casefold()
    if backend == "redis":
        url = str(getattr(settings, "research_queue_redis_url", "") or getattr(settings, "rate_limit_redis_url", ""))
        if not url:
            raise ValueError("RESEARCH_QUEUE_REDIS_URL or RATE_LIMIT_REDIS_URL is required for redis backend")
        return RedisResearchJobQueue(
            url=url,
            max_pending=int(getattr(settings, "research_queue_max_pending", 32)),
            timeout_seconds=float(getattr(settings, "research_queue_timeout_seconds", 90.0)),
            ttl_seconds=int(getattr(settings, "research_queue_ttl_seconds", 900)),
        )
    if backend != "memory":
        raise ValueError(f"unsupported research queue backend: {backend}")
    return ResearchJobQueue(
        max_pending=int(getattr(settings, "research_queue_max_pending", 32)),
        max_workers=int(getattr(settings, "research_queue_max_workers", 2)),
        timeout_seconds=float(getattr(settings, "research_queue_timeout_seconds", 90.0)),
        ttl_seconds=int(getattr(settings, "research_queue_ttl_seconds", 900)),
    )
