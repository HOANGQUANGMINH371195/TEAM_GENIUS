"""Durable VBPL import jobs and stage state."""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import text

from src.db.session import session_scope
from src.services.vbpl_ingest import VbplIngestService
from src.services.vbpl_session import VbplSessionManager

STAGES = ("database", "embedding", "relationships")

def _job_key(doc_ids: list[str], requested_by: str, supplied: str = "") -> str:
    supplied_key = supplied.strip()
    if supplied_key:
        # Preserve explicit client idempotency across transport retries. Hash
        # oversized headers instead of truncating distinct keys into a collision.
        return (
            supplied_key
            if len(supplied_key) <= 255
            else f"header:{hashlib.sha256(supplied_key.encode('utf-8')).hexdigest()}"
        )
    # A document can be intentionally re-imported after cleanup or a corrected
    # source snapshot. Deterministic keys without an explicit client key would
    # resurrect an old terminal job forever; callers that need deduplication
    # must send Idempotency-Key.
    return f"request:{uuid.uuid4().hex}"  # noqa: S311


class VbplJobService:
    @classmethod
    async def create_job(
        cls,
        doc_ids: list[str],
        *,
        requested_by: str = "",
        request_id: str = "",
        idempotency_key: str = "",
        trigger: str = "manual",
    ) -> dict[str, Any]:
        unique_ids = list(dict.fromkeys(doc_ids))
        if not unique_ids:
            raise ValueError("At least one document ID is required")
        key = _job_key(unique_ids, requested_by, idempotency_key)
        async with session_scope() as session:
            dataset = await session.execute(
                text("SELECT active_dataset_id FROM public.dataset_state WHERE singleton = true")
            )
            dataset_id = dataset.scalar_one_or_none()
            if not dataset_id:
                raise RuntimeError("No active dataset is configured")

            # Claim the idempotency key atomically. A check-then-insert race
            # would turn two browser retries into a unique-constraint 500.
            job_id = uuid.uuid4()
            inserted = await session.execute(
                text("""
                INSERT INTO public.vbpl_ingest_jobs
                    (job_id, dataset_id, idempotency_key, requested_by, trigger,
                     total_items, request_id)
                VALUES (:job_id, :dataset_id, :idempotency_key, :requested_by,
                        :trigger, :total_items, :request_id)
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING job_id
                """),
                {
                    "job_id": job_id,
                    "dataset_id": dataset_id,
                    "idempotency_key": key,
                    "requested_by": requested_by[:255],
                    "trigger": trigger,
                    "total_items": len(unique_ids),
                    "request_id": request_id[:128],
                },
            )
            inserted_id = inserted.scalar_one_or_none()
            if inserted_id is None:
                existing = await session.execute(
                    text("SELECT job_id FROM public.vbpl_ingest_jobs WHERE idempotency_key = :key"),
                    {"key": key},
                )
                existing_id = existing.scalar_one_or_none()
                if existing_id is None:
                    raise RuntimeError("VBPL idempotency record disappeared")
                return await cls.get_job(str(existing_id), session=session)
            for doc_id in unique_ids:
                await session.execute(
                    text("""
                    INSERT INTO public.vbpl_ingest_items (job_id, doc_id)
                    VALUES (:job_id, :doc_id)
                    """),
                    {"job_id": job_id, "doc_id": doc_id},
                )
                for stage in STAGES:
                    await session.execute(
                        text("""
                        INSERT INTO public.vbpl_ingest_stages (job_id, doc_id, stage)
                        VALUES (:job_id, :doc_id, :stage)
                        """),
                        {"job_id": job_id, "doc_id": doc_id, "stage": stage},
                    )
            await session.commit()
        return await cls.get_job(str(job_id))

    @classmethod
    async def get_job(cls, job_id: str, *, session=None) -> dict[str, Any]:
        owned = session is None
        scope = session_scope() if owned else _SessionPassthrough(session)
        async with scope as current:
            result = await current.execute(
                text("""
                SELECT j.job_id, j.dataset_id, j.status, j.requested_by, j.trigger,
                       j.total_items, j.succeeded_items, j.failed_items,
                       j.error_message, j.created_at, j.started_at, j.finished_at,
                       i.doc_id, i.status AS item_status, i.current_stage,
                       i.chunks_count, i.error_message AS item_error,
                       s.stage, s.status AS stage_status, s.attempt,
                       s.started_at AS stage_started_at, s.finished_at AS stage_finished_at,
                       s.metrics, s.error_code, s.error_message AS stage_error
                FROM public.vbpl_ingest_jobs j
                LEFT JOIN vbpl_ingest_items i ON i.job_id = j.job_id
                LEFT JOIN vbpl_ingest_stages s ON s.job_id = i.job_id AND s.doc_id = i.doc_id
                WHERE j.job_id = :job_id
                ORDER BY i.doc_id, CASE s.stage
                    WHEN 'database' THEN 1 WHEN 'embedding' THEN 2 WHEN 'relationships' THEN 3 ELSE 4 END
                """),
                {"job_id": job_id},
            )
            rows = result.mappings().all()
            if not rows:
                raise KeyError("Job not found")
            first = rows[0]
            items: dict[str, dict[str, Any]] = {}
            for row in rows:
                item = items.setdefault(
                    str(row["doc_id"]),
                    {
                        "doc_id": str(row["doc_id"]),
                        "status": row["item_status"],
                        "current_stage": row["current_stage"],
                        "chunks_count": row["chunks_count"],
                        "error": row["item_error"] or "",
                        "stages": [],
                    },
                )
                if row["stage"]:
                    item["stages"].append(
                        {
                            "stage": row["stage"],
                            "status": row["stage_status"],
                            "attempt": row["attempt"],
                            "started_at": row["stage_started_at"],
                            "finished_at": row["stage_finished_at"],
                            "metrics": row["metrics"] or {},
                            "error_code": row["error_code"] or "",
                            "error": row["stage_error"] or "",
                            "retryable": row["stage_status"] == "failed",
                        }
                    )
            return {
                "job_id": str(first["job_id"]),
                "dataset_id": first["dataset_id"],
                "status": first["status"],
                "requested_by": first["requested_by"],
                "trigger": first["trigger"],
                "total_items": first["total_items"],
                "succeeded_items": first["succeeded_items"],
                "failed_items": first["failed_items"],
                "error": first["error_message"] or "",
                "created_at": first["created_at"],
                "started_at": first["started_at"],
                "finished_at": first["finished_at"],
                "items": list(items.values()),
            }

    @classmethod
    async def process_job(cls, job_id: str) -> dict[str, Any]:
        """Claim and process one job; each item remains observable after failures."""
        async with session_scope() as session:
            claim = await session.execute(
                text("""
                UPDATE public.vbpl_ingest_jobs
                SET status = 'running',
                    lease_until = now() + interval '15 minutes',
                    heartbeat_at = now(),
                    started_at = COALESCE(started_at, now())
                WHERE job_id = :job_id
                  AND (
                      status = 'queued'
                      OR (status = 'running' AND lease_until IS NOT NULL AND lease_until < now())
                  )
                RETURNING job_id
                """),
                {"job_id": job_id},
            )
            claimed = claim.scalar_one_or_none()
            if claimed is None:
                existing = await session.execute(
                    text("SELECT status FROM public.vbpl_ingest_jobs WHERE job_id = :job_id"),
                    {"job_id": job_id},
                )
                current_status = existing.scalar_one_or_none()
                if current_status is None:
                    raise KeyError("Job not found")
                # Another worker owns active job, or job already reached terminal state.
                await session.commit()
                return await cls.get_job(job_id)
            rows = await session.execute(
                text("SELECT doc_id FROM public.vbpl_ingest_items WHERE job_id = :job_id ORDER BY doc_id"),
                {"job_id": job_id},
            )
            doc_ids = [str(row[0]) for row in rows.all()]
            if not doc_ids:
                raise KeyError("Job not found")
            await session.commit()

        for doc_id in doc_ids:
            try:
                await cls._process_item(job_id, doc_id)
            except Exception as error:
                await cls._record_item_failure(job_id, doc_id, error)
        return await cls._finish_job(job_id)

    @classmethod
    async def _record_item_failure(cls, job_id: str, doc_id: str, error: Exception) -> None:
        message = str(error).replace("\\r", " ").replace("\\n", " ").strip()[:1000] or error.__class__.__name__
        async with session_scope() as session:
            await session.execute(
                text("UPDATE public.vbpl_ingest_items SET status = 'failed', error_message = :error WHERE job_id = :job_id AND doc_id = :doc_id"),
                {"job_id": job_id, "doc_id": doc_id, "error": message},
            )
            await session.execute(
                text("""
                UPDATE public.vbpl_ingest_stages
                SET status = CASE WHEN status = 'pending' THEN 'skipped' ELSE status END,
                    finished_at = CASE WHEN status = 'pending' THEN now() ELSE finished_at END,
                    error_code = CASE WHEN status = 'pending' THEN 'blocked' ELSE error_code END,
                    error_message = CASE WHEN status = 'pending' THEN :error ELSE error_message END
                WHERE job_id = :job_id AND doc_id = :doc_id
                """),
                {"job_id": job_id, "doc_id": doc_id, "error": message},
            )
            await session.execute(
                text("UPDATE public.vbpl_ingest_jobs SET failed_items = failed_items + 1, heartbeat_at = now() WHERE job_id = :job_id"),
                {"job_id": job_id},
            )
            await session.commit()

    @classmethod
    async def _process_item(cls, job_id: str, doc_id: str) -> None:
        detail = await VbplSessionManager.fetch_document_detail(doc_id)
        async with session_scope() as session:
            await session.execute(
                text("UPDATE public.vbpl_ingest_items SET detail_payload = CAST(:payload AS jsonb), current_stage = 'database' WHERE job_id = :job_id AND doc_id = :doc_id"),
                {"payload": json.dumps(detail), "job_id": job_id, "doc_id": doc_id},
            )
            await session.commit()
        result = await VbplIngestService.ingest_document(doc_id, detail)
        await cls._record_legacy_result(job_id, doc_id, result)

    @classmethod
    async def _record_legacy_result(cls, job_id: str, doc_id: str, result: dict[str, Any]) -> None:
        statuses = {
            "database": result.get("supabase", "failed"),
            "embedding": result.get("qdrant", "failed"),
            "relationships": result.get("neo4j", "failed"),
        }
        # A downstream stage is never allowed to appear successful when an
        # earlier source-of-truth stage failed. Mark it blocked/skipped.
        blocked = False
        async with session_scope() as session:
            for stage, stage_status in statuses.items():
                if blocked:
                    normalized = "skipped"
                    error_code = "blocked"
                else:
                    normalized = "succeeded" if stage_status == "success" else "failed" if stage_status == "failed" else "skipped"
                    error_code = "" if normalized != "failed" else "provider_error"
                    if normalized != "succeeded":
                        blocked = True
                await session.execute(
                    text("""
                    UPDATE public.vbpl_ingest_stages
                    SET status = :status, attempt = attempt + 1,
                        finished_at = now(), error_code = :error_code,
                        error_message = :error
                    WHERE job_id = :job_id AND doc_id = :doc_id AND stage = :stage
                    """),
                    {"status": normalized, "error_code": error_code, "error": result.get("error", "")[:1000], "job_id": job_id, "doc_id": doc_id, "stage": stage},
                )
            item_status = "succeeded" if result.get("status") == "success" else "partial" if result.get("status") == "partial" else "failed"
            current_stage = "relationships" if item_status == "succeeded" else next((stage for stage, stage_status in statuses.items() if stage_status != "success"), "database")
            await session.execute(
                text("UPDATE public.vbpl_ingest_items SET status = :status, current_stage = :stage, chunks_count = :chunks, error_message = :error WHERE job_id = :job_id AND doc_id = :doc_id"),
                {"status": item_status, "stage": current_stage, "chunks": result.get("chunks_count", 0), "error": result.get("error", "")[:1000], "job_id": job_id, "doc_id": doc_id},
            )
            await session.commit()

    @classmethod
    async def fail_job(cls, job_id: str, error: str) -> None:
        """Record an unexpected worker failure without exposing provider details."""
        safe_error = str(error).replace("\r", " ").replace("\n", " ").strip()[:1000]
        async with session_scope() as session:
            await session.execute(
                text("""
                UPDATE public.vbpl_ingest_jobs
                SET status = 'failed', error_message = :error, finished_at = now()
                WHERE job_id = :job_id
                """),
                {"job_id": job_id, "error": safe_error},
            )
            await session.execute(
                text("""
                UPDATE public.vbpl_ingest_items
                SET status = 'failed', error_message = :error
                WHERE job_id = :job_id AND status IN ('queued', 'running')
                """),
                {"job_id": job_id, "error": safe_error},
            )
            await session.commit()

    @classmethod
    async def _finish_job(cls, job_id: str) -> dict[str, Any]:
        async with session_scope() as session:
            counts = await session.execute(text("SELECT count(*) FILTER (WHERE status = 'succeeded'), count(*) FILTER (WHERE status IN ('partial','failed')) FROM public.vbpl_ingest_items WHERE job_id = :job_id"), {"job_id": job_id})
            succeeded, failed = counts.one()
            total = await session.scalar(text("SELECT total_items FROM public.vbpl_ingest_jobs WHERE job_id = :job_id"), {"job_id": job_id})
            status = "succeeded" if succeeded == total else "partial" if succeeded else "failed"
            await session.execute(text("UPDATE public.vbpl_ingest_jobs SET status = :status, succeeded_items = :succeeded, failed_items = :failed, finished_at = now() WHERE job_id = :job_id"), {"status": status, "succeeded": succeeded, "failed": failed, "job_id": job_id})
            await session.commit()
        return await cls.get_job(job_id)

    @classmethod
    async def retry_item(cls, job_id: str, doc_id: str, from_stage: str | None = None) -> dict[str, Any]:
        if from_stage is not None and from_stage not in STAGES:
            raise ValueError("Invalid stage")
        async with session_scope() as session:
            params = {"job_id": job_id, "doc_id": doc_id}
            if from_stage:
                index = STAGES.index(from_stage)
                stage_names = STAGES[index:]
            else:
                stage_names = STAGES
            await session.execute(
                text("UPDATE public.vbpl_ingest_stages SET status = 'pending', error_message = '', finished_at = NULL WHERE job_id = :job_id AND doc_id = :doc_id AND stage = ANY(:stages)"),
                {**params, "stages": stage_names},
            )
            await session.execute(text("UPDATE public.vbpl_ingest_items SET status = 'queued', error_message = '' WHERE job_id = :job_id AND doc_id = :doc_id"), params)
            await session.execute(text("UPDATE public.vbpl_ingest_jobs SET status = 'queued', finished_at = NULL, trigger = 'retry' WHERE job_id = :job_id"), {"job_id": job_id})
            await session.commit()
        return await cls.get_job(job_id)


class _SessionPassthrough:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_):
        return False
