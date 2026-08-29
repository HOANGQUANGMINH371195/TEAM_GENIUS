"""VBPL discovery synchronization backed by short-lived Redis state."""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from src.services.vbpl_cache import VbplCache
from src.services.vbpl_session import VbplSessionManager

HEALTH_TERMS = (
    "bảo hiểm y tế",
    "viện phí",
    "bhyt",
    "khám chữa bệnh",
    "bảo hiểm xã hội",
)
SYNC_LOCK = "vbpl:discovery:sync"


def _now() -> datetime:
    return datetime.now(UTC)


def _safe_error(error: Exception) -> str:
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


def _health_related(item: dict[str, Any]) -> bool:
    value = " ".join(
        str(item.get(key) or "")
        for key in ("title", "summary", "docNum", "agencyName")
    ).casefold()
    return any(term in value for term in HEALTH_TERMS)


def normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    doc_type = item.get("docType") or {}
    status = item.get("effStatus") or {}
    doc_id = str(item.get("id") or item.get("docId") or "").strip()
    return {
        "doc_id": doc_id,
        "title": str(item.get("title") or ""),
        "so_ky_hieu": str(item.get("docNum") or item.get("soKyHieu") or ""),
        "issue_date": str(item.get("issueDate") or ""),
        "issuing_body": str(item.get("agencyName") or ""),
        "doc_type": (
            str(doc_type.get("name") or "")
            if isinstance(doc_type, dict)
            else str(doc_type)
        ),
        "legal_status": (
            str(status.get("name") or "")
            if isinstance(status, dict)
            else str(status)
        ),
        "summary": str(item.get("summary") or ""),
        "is_health_related": _health_related(item),
        "ingestion_status": "not_imported",
    }


class VbplSyncService:
    @classmethod
    async def sync_latest(cls, refresh_id: str = "") -> dict[str, Any]:
        refresh_id = refresh_id or hashlib.sha256(
            str(_now()).encode("utf-8")
        ).hexdigest()[:24]
        state: dict[str, Any] = {
            "refresh_id": refresh_id,
            "status": "running",
            "poll_url": refresh_id,
            "error": "",
            "items_count": 0,
        }
        await VbplCache.set_sync(refresh_id, state)
        async with VbplCache.lock(SYNC_LOCK, 300) as acquired:
            if not acquired:
                state.update(
                    {
                        "status": "failed",
                        "error": "Another VBPL sync is already running",
                    }
                )
                await VbplCache.set_sync(refresh_id, state)
                return state
            await VbplCache.set_sync(refresh_id, state)
            try:
                raw_items = await VbplSessionManager.fetch_with_fallback()
                items = [
                    normalized
                    for raw in raw_items
                    if isinstance(raw, dict)
                    and (normalized := normalize_item(raw))["doc_id"]
                ]
                synced_at = _now().isoformat()
                payload = {
                    "items": items,
                    "last_synced_at": synced_at,
                    "stale": False,
                    "refresh_status": "succeeded",
                }
                await VbplCache.set_discovery(payload)
                state.update(
                    {
                        "status": "succeeded",
                        "items_count": len(items),
                        "last_synced_at": synced_at,
                    }
                )
            except Exception as error:
                state.update({"status": "failed", "error": _safe_error(error)})
        await VbplCache.set_sync(refresh_id, state)
        return state

    @classmethod
    async def cached_discovery(cls) -> dict[str, Any]:
        value = await VbplCache.get_discovery()
        if not isinstance(value, dict):
            return {
                "items": [],
                "last_synced_at": None,
                "stale": True,
                "refresh_status": "idle",
            }
        return {
            "items": value.get("items", []),
            "last_synced_at": value.get("last_synced_at"),
            "stale": bool(value.get("stale", False)),
            "refresh_status": value.get("refresh_status", "idle"),
        }

    @classmethod
    async def get_status(cls, refresh_id: str) -> dict[str, Any] | None:
        value = await VbplCache.get_sync(refresh_id)
        return value if isinstance(value, dict) else None
