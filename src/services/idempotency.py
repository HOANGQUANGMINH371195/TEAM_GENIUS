"""PostgreSQL-backed idempotency boundary for safe client retries."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from src.db.session import session_scope

logger = logging.getLogger(__name__)
IdempotencyState = Literal["new", "replay", "conflict", "in_progress", "disabled"]


@dataclass(frozen=True)
class IdempotencyDecision:
    state: IdempotencyState
    request_id: str = ""
    response: dict[str, Any] | None = None


class IdempotencyStore:
    """Small SQL adapter; absence of the additive migration is fail-open."""

    async def begin(
        self,
        *,
        owner_uid: str,
        endpoint: str,
        key: str,
        request_hash: str,
        request_id: str,
    ) -> IdempotencyDecision:
        if not owner_uid or not endpoint or not key:
            return IdempotencyDecision("disabled")
        if len(key) < 8 or len(key) > 128:
            return IdempotencyDecision("conflict")
        try:
            async with session_scope() as session:
                inserted = await session.execute(
                    text(
                        """
                        INSERT INTO public.idempotency_records(
                            owner_uid, endpoint, idempotency_key, request_hash,
                            status, request_id
                        ) VALUES (:owner_uid, :endpoint, :key, :request_hash, 'processing', :request_id)
                        ON CONFLICT (owner_uid, endpoint, idempotency_key) DO NOTHING
                        RETURNING request_id
                        """
                    ),
                    {
                        "owner_uid": owner_uid,
                        "endpoint": endpoint,
                        "key": key,
                        "request_hash": request_hash,
                        "request_id": request_id[:128],
                    },
                )
                created = inserted.scalar_one_or_none()
                if created is not None:
                    await session.commit()
                    return IdempotencyDecision("new", request_id=str(created))
                existing = await session.execute(
                    text(
                        """
                        SELECT request_hash, status, request_id, response, expires_at <= now() AS expired
                        FROM public.idempotency_records
                        WHERE owner_uid = :owner_uid AND endpoint = :endpoint
                          AND idempotency_key = :key
                        FOR UPDATE
                        """
                    ),
                    {"owner_uid": owner_uid, "endpoint": endpoint, "key": key},
                )
                row = existing.mappings().one_or_none()
                if row is None:
                    await session.rollback()
                    return IdempotencyDecision("new", request_id=request_id)
                if bool(row["expired"]):
                    await session.execute(
                        text(
                            "DELETE FROM public.idempotency_records WHERE owner_uid = :owner_uid "
                            "AND endpoint = :endpoint AND idempotency_key = :key"
                        ),
                        {"owner_uid": owner_uid, "endpoint": endpoint, "key": key},
                    )
                    await session.execute(
                        text(
                            """
                            INSERT INTO public.idempotency_records(
                                owner_uid, endpoint, idempotency_key, request_hash,
                                status, request_id
                            ) VALUES (:owner_uid, :endpoint, :key, :request_hash, 'processing', :request_id)
                            """
                        ),
                        {
                            "owner_uid": owner_uid,
                            "endpoint": endpoint,
                            "key": key,
                            "request_hash": request_hash,
                            "request_id": request_id[:128],
                        },
                    )
                    await session.commit()
                    return IdempotencyDecision("new", request_id=request_id)
                if str(row["request_hash"]) != request_hash:
                    await session.rollback()
                    return IdempotencyDecision("conflict", request_id=str(row["request_id"] or ""))
                if row["status"] == "completed":
                    payload = row["response"]
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    await session.rollback()
                    return IdempotencyDecision(
                        "replay",
                        request_id=str(row["request_id"] or ""),
                        response=dict(payload) if isinstance(payload, dict) else None,
                    )
                await session.rollback()
                return IdempotencyDecision("in_progress", request_id=str(row["request_id"] or ""))
        except ProgrammingError as exc:
            if "idempotency" in str(exc).casefold():
                logger.warning("Idempotency migration is unavailable; retry protection disabled")
                return IdempotencyDecision("disabled")
            raise

    async def complete(
        self,
        *,
        owner_uid: str,
        endpoint: str,
        key: str,
        request_id: str,
        response: dict[str, Any],
    ) -> None:
        if not owner_uid or not key:
            return
        try:
            async with session_scope() as session:
                await session.execute(
                    text(
                        """
                        UPDATE public.idempotency_records
                        SET status = 'completed', request_id = :request_id,
                            response = CAST(:response AS jsonb)
                        WHERE owner_uid = :owner_uid AND endpoint = :endpoint
                          AND idempotency_key = :key AND status = 'processing'
                        """
                    ),
                    {
                        "owner_uid": owner_uid,
                        "endpoint": endpoint,
                        "key": key,
                        "request_id": request_id[:128],
                        "response": json.dumps(response, ensure_ascii=False, separators=(",", ":")),
                    },
                )
                await session.commit()
        except ProgrammingError as exc:
            if "idempotency" not in str(exc).casefold():
                raise
            logger.warning("Idempotency completion skipped because migration is unavailable")

    async def abort(self, *, owner_uid: str, endpoint: str, key: str) -> None:
        if not owner_uid or not key:
            return
        try:
            async with session_scope() as session:
                await session.execute(
                    text(
                        "DELETE FROM public.idempotency_records WHERE owner_uid = :owner_uid "
                        "AND endpoint = :endpoint AND idempotency_key = :key AND status = 'processing'"
                    ),
                    {"owner_uid": owner_uid, "endpoint": endpoint, "key": key},
                )
                await session.commit()
        except ProgrammingError as exc:
            if "idempotency" not in str(exc).casefold():
                raise


_store = IdempotencyStore()


def get_idempotency_store() -> IdempotencyStore:
    return _store
