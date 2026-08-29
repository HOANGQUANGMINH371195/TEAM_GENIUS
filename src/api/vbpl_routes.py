from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.api.auth import require_admin
from src.models.schemas import (
    VbplDiscoveryResponse,
    VbplDocumentDetail,
    VbplIngestAccepted,
    VbplIngestJob,
    VbplIngestRequest,
    VbplRefreshResponse,
    VbplRetryRequest,
    VbplSyncStatus,
)
from src.services.vbpl_jobs import VbplJobService
from src.services.vbpl_session import VbplSessionError, VbplSessionManager
from src.services.vbpl_sync import VbplSyncService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/admin/vbpl", tags=["Admin VBPL"])
# Local fallback for development. Durable PostgreSQL job rows remain the
# source of truth and the dedicated worker claims work after API restarts.
_sync_tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}
_job_tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}


def _safe_error(error: Exception) -> str:
    """Return bounded client-safe error text without connection secrets."""
    value = str(error).replace("\r", " ").replace("\n", " ").strip()
    lowered = value.casefold()
    for marker in (
        "postgresql+asyncpg://",
        "postgresql://",
        "password=",
        "api_key=",
        "authorization:",
        "cookie:",
    ):
        index = lowered.find(marker.casefold())
        if index >= 0:
            value = value[:index].rstrip(" ,;:")
            lowered = value.casefold()
    return value[:1000] or error.__class__.__name__


def _poll_url(request: Request, path: str) -> str:
    # Relative URLs survive reverse proxies and preserve browser origin.
    return path


def _job_accepted(job: dict[str, Any], request: Request) -> VbplIngestAccepted:
    job_id = str(job["job_id"])
    return VbplIngestAccepted(
        job_id=job_id,
        dataset_id=str(job.get("dataset_id") or ""),
        status=job.get("status", "queued"),
        poll_url=_poll_url(request, f"/api/v1/auth/admin/vbpl/jobs/{job_id}"),
    )


def _with_job_poll_url(job: dict[str, Any], request: Request) -> dict[str, Any]:
    payload = dict(job)
    payload["poll_url"] = _poll_url(request, f"/api/v1/auth/admin/vbpl/jobs/{job['job_id']}")
    return payload


async def _run_sync(refresh_id: str) -> dict[str, Any]:
    try:
        return await VbplSyncService.sync_latest(refresh_id)
    except Exception as error:  # pragma: no cover - defensive task boundary
        logger.exception("VBPL sync task failed", extra={"refresh_id": refresh_id})
        return {
            "refresh_id": refresh_id,
            "status": "failed",
            "poll_url": refresh_id,
            "error": _safe_error(error),
            "items_count": 0,
        }
    finally:
        _sync_tasks.pop(refresh_id, None)


async def _run_job(job_id: str) -> dict[str, Any]:
    try:
        return await VbplJobService.process_job(job_id)
    except Exception as error:  # pragma: no cover - defensive task boundary
        logger.exception("VBPL job task failed", extra={"job_id": job_id})
        try:
            await VbplJobService.fail_job(job_id, _safe_error(error))
            return await VbplJobService.get_job(job_id)
        except Exception:
            return {"job_id": job_id, "status": "failed", "error": _safe_error(error)}
    finally:
        _job_tasks.pop(job_id, None)


def _schedule_sync(refresh_id: str) -> None:
    existing = _sync_tasks.get(refresh_id)
    if existing and not existing.done():
        return
    _sync_tasks[refresh_id] = asyncio.create_task(_run_sync(refresh_id))


def _schedule_job(job_id: str) -> None:
    existing = _job_tasks.get(job_id)
    if existing and not existing.done():
        return
    _job_tasks[job_id] = asyncio.create_task(_run_job(job_id))


@router.get(
    "/discover",
    response_model=VbplDiscoveryResponse,
    dependencies=[Depends(require_admin)],
)
async def discover_vbpl() -> VbplDiscoveryResponse:
    try:
        cached = await VbplSyncService.cached_discovery()
        if not cached.get("last_synced_at"):
            result = await VbplSyncService.sync_latest(uuid.uuid4().hex)
            if result.get("status") == "failed":
                raise VbplSessionError("VBPL source session unavailable")
            cached = await VbplSyncService.cached_discovery()
        return VbplDiscoveryResponse(**cached)
    except VbplSessionError as error:
        logger.exception("VBPL session expired during discover")
        raise HTTPException(status_code=503, detail="VBPL source session unavailable") from error
    except Exception as error:
        logger.exception("VBPL discovery failed")
        raise HTTPException(status_code=500, detail="VBPL discovery failed") from error


@router.post(
    "/sync",
    response_model=VbplRefreshResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_admin)],
)
async def sync_vbpl(request: Request) -> VbplRefreshResponse:
    refresh_id = uuid.uuid4().hex
    _schedule_sync(refresh_id)
    return VbplRefreshResponse(
        refresh_id=refresh_id,
        status="queued",
        poll_url=_poll_url(request, f"/api/v1/auth/admin/vbpl/sync/{refresh_id}"),
    )


@router.get(
    "/sync/{refresh_id}",
    response_model=VbplSyncStatus,
    dependencies=[Depends(require_admin)],
)
async def get_vbpl_sync(refresh_id: str, request: Request) -> VbplSyncStatus:
    state = await VbplSyncService.get_status(refresh_id)
    if state is None:
        raise HTTPException(status_code=404, detail="VBPL sync run not found")
    state["poll_url"] = _poll_url(request, f"/api/v1/auth/admin/vbpl/sync/{refresh_id}")
    return VbplSyncStatus(**state)


@router.get(
    "/detail/{doc_id}",
    response_model=VbplDocumentDetail,
    dependencies=[Depends(require_admin)],
)
async def get_vbpl_detail(doc_id: str) -> VbplDocumentDetail:
    try:
        data = await VbplSessionManager.fetch_document_detail(doc_id)
        return VbplDocumentDetail(
            doc_id=doc_id,
            title=data.get("title", ""),
            so_ky_hieu=data.get("doc_num", ""),
            content_html=data.get("content_html", ""),
            relationships=data.get("references", []),
            metadata=data,
        )
    except Exception as error:
        logger.exception("VBPL detail fetch failed for %s", doc_id)
        raise HTTPException(status_code=502, detail="VBPL detail fetch failed") from error


@router.post(
    "/ingest",
    response_model=VbplIngestAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_vbpl(
    request: Request,
    body: VbplIngestRequest,
    admin: dict[str, Any] = Depends(require_admin),
) -> VbplIngestAccepted:
    try:
        job = await VbplJobService.create_job(
            body.doc_ids,
            requested_by=str(admin.get("uid") or ""),
            request_id=str(getattr(request.state, "request_id", "") or ""),
            idempotency_key=request.headers.get("Idempotency-Key", ""),
            trigger="manual",
        )
        _schedule_job(str(job["job_id"]))
        return _job_accepted(job, request)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail="VBPL import is not ready") from error
    except Exception as error:
        logger.exception("VBPL ingest job creation failed")
        raise HTTPException(status_code=500, detail="VBPL ingest job creation failed") from error


@router.get(
    "/jobs/{job_id}",
    response_model=VbplIngestJob,
    dependencies=[Depends(require_admin)],
)
async def get_vbpl_job(job_id: str, request: Request) -> VbplIngestJob:
    try:
        return VbplIngestJob(**_with_job_poll_url(await VbplJobService.get_job(job_id), request))
    except KeyError as error:
        raise HTTPException(status_code=404, detail="VBPL ingest job not found") from error
    except Exception as error:
        logger.exception("VBPL job read failed for %s", job_id)
        raise HTTPException(status_code=500, detail="VBPL ingest job unavailable") from error


@router.post(
    "/jobs/{job_id}/items/{doc_id}/retry",
    response_model=VbplIngestAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_vbpl_item(
    job_id: str,
    doc_id: str,
    request: Request,
    body: VbplRetryRequest | None = None,
    _admin: dict[str, Any] = Depends(require_admin),
) -> VbplIngestAccepted:
    try:
        job = await VbplJobService.retry_item(job_id, doc_id, body.from_stage if body else None)
        _schedule_job(job_id)
        return _job_accepted(job, request)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="VBPL ingest item not found") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        logger.exception("VBPL item retry failed for %s/%s", job_id, doc_id)
        raise HTTPException(status_code=500, detail="VBPL retry unavailable") from error


@router.post(
    "/jobs/{job_id}/retry",
    response_model=VbplIngestAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_vbpl_job(
    job_id: str,
    request: Request,
    body: VbplRetryRequest | None = None,
    _admin: dict[str, Any] = Depends(require_admin),
) -> VbplIngestAccepted:
    try:
        current = await VbplJobService.get_job(job_id)
        items = [item for item in current.get("items", []) if item.get("status") in {"failed", "partial"}]
        if not items:
            items = current.get("items", [])
        job = current
        for item in items:
            job = await VbplJobService.retry_item(job_id, str(item["doc_id"]), body.from_stage if body else None)
        _schedule_job(job_id)
        return _job_accepted(job, request)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="VBPL ingest job not found") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        logger.exception("VBPL job retry failed for %s", job_id)
        raise HTTPException(status_code=500, detail="VBPL retry unavailable") from error


@router.post("/refresh-session", dependencies=[Depends(require_admin)])
async def refresh_vbpl_session() -> dict[str, bool]:
    try:
        await VbplSessionManager.refresh_session()
        return {"ok": True}
    except Exception as error:
        logger.exception("VBPL session refresh failed")
        raise HTTPException(status_code=500, detail="VBPL session refresh failed") from error
