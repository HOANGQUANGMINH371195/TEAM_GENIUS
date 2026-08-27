"""Immutable offline batch manifest and cost ledger primitives.

These classes deliberately stop before provider submission. They make batch
jobs reproducible and idempotent; an adapter for a specific provider can submit
the serialized records without changing the accounting contract.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def estimate_tokens(value: str) -> int:
    """Conservative, provider-independent token estimate for budgeting."""
    return max(1, (len(value) + 3) // 4)


@dataclass
class BatchItem:
    item_id: str
    input_sha256: str
    release_id: str
    model: str
    payload: dict[str, Any]
    estimated_input_tokens: int
    estimated_cost_usd: float
    status: str = "pending"
    attempts: int = 0
    output_tokens: int = 0
    actual_cost_usd: float | None = None
    error_class: str = ""
    started_at: str | None = None
    finished_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "input_sha256": self.input_sha256,
            "release_id": self.release_id,
            "model": self.model,
            "payload": self.payload,
            "estimated_input_tokens": self.estimated_input_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 8),
            "status": self.status,
            "attempts": self.attempts,
            "output_tokens": self.output_tokens,
            "actual_cost_usd": self.actual_cost_usd,
            "error_class": self.error_class,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


@dataclass
class CostLedger:
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    actual_cost_usd: float = 0.0

    def add(self, item: BatchItem, *, actual_cost_usd: float | None = None) -> None:
        self.input_tokens += item.estimated_input_tokens
        self.output_tokens += item.output_tokens
        self.estimated_cost_usd += item.estimated_cost_usd
        self.actual_cost_usd += (
            actual_cost_usd if actual_cost_usd is not None else item.actual_cost_usd or item.estimated_cost_usd
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 8),
            "actual_cost_usd": round(self.actual_cost_usd, 8),
        }


@dataclass
class BatchManifest:
    manifest_id: str
    release_id: str
    model: str
    created_at: str
    items: list[BatchItem] = field(default_factory=list)

    @classmethod
    def build(
        cls,
        payloads: list[dict[str, Any]],
        *,
        release_id: str,
        model: str,
        input_usd_per_million: float = 0.0,
    ) -> BatchManifest:
        if not release_id.strip() or not model.strip():
            raise ValueError("release_id and model are required")
        items: list[BatchItem] = []
        seen: set[str] = set()
        for index, payload in enumerate(payloads):
            canonical = _canonical(payload)
            digest = _sha256(f"{release_id}\x00{model}\x00{canonical}")
            if digest in seen:
                continue
            seen.add(digest)
            input_tokens = estimate_tokens(canonical)
            items.append(
                BatchItem(
                    item_id=f"item-{digest[:24]}",
                    input_sha256=_sha256(canonical),
                    release_id=release_id,
                    model=model,
                    payload=payload,
                    estimated_input_tokens=input_tokens,
                    estimated_cost_usd=input_tokens / 1_000_000 * max(0.0, input_usd_per_million),
                )
            )
        manifest_key = _canonical(
            {"release_id": release_id, "model": model, "items": [item.item_id for item in items]}
        )
        return cls(
            manifest_id=f"batch-{_sha256(manifest_key)[:24]}",
            release_id=release_id,
            model=model,
            created_at=datetime.now(UTC).isoformat(),
            items=items,
        )

    def mark_result(
        self,
        item_id: str,
        *,
        output_tokens: int,
        actual_cost_usd: float | None = None,
    ) -> None:
        item = self._item(item_id)
        if item.status == "complete":
            return
        item.attempts += 1
        item.started_at = item.started_at or datetime.now(UTC).isoformat()
        item.output_tokens = max(0, int(output_tokens))
        item.actual_cost_usd = actual_cost_usd
        item.status = "complete"
        item.error_class = ""
        item.finished_at = datetime.now(UTC).isoformat()

    def mark_error(self, item_id: str, error_class: str, *, max_attempts: int = 3) -> None:
        item = self._item(item_id)
        if item.status == "complete":
            return
        item.attempts += 1
        item.started_at = item.started_at or datetime.now(UTC).isoformat()
        item.error_class = error_class[:120]
        item.status = "quarantined" if item.attempts >= max(1, max_attempts) else "retryable_error"
        item.finished_at = datetime.now(UTC).isoformat()

    def ledger(self) -> CostLedger:
        ledger = CostLedger()
        for item in self.items:
            ledger.add(item)
        return ledger

    def as_dict(self) -> dict[str, object]:
        return {
            "manifest_id": self.manifest_id,
            "release_id": self.release_id,
            "model": self.model,
            "created_at": self.created_at,
            "items": [item.as_dict() for item in self.items],
            "ledger": self.ledger().as_dict(),
        }

    def to_jsonl(self) -> str:
        rows = [{"manifest": {key: value for key, value in self.as_dict().items() if key != "items"}}]
        rows.extend({"item": item.as_dict()} for item in self.items)
        return "\n".join(_canonical(row) for row in rows) + "\n"

    def to_provider_jsonl(self, *, endpoint: str = "/v1/responses") -> str:
        """Serialize request lines accepted by a provider Batch API."""
        if not endpoint.startswith("/"):
            raise ValueError("endpoint must be an absolute API path")
        rows = [
            {
                "custom_id": item.item_id,
                "method": "POST",
                "url": endpoint,
                "body": {"model": self.model, **item.payload},
            }
            for item in self.items
            if item.status not in {"complete", "quarantined"}
        ]
        return "\n".join(_canonical(row) for row in rows) + ("\n" if rows else "")

    def _item(self, item_id: str) -> BatchItem:
        for item in self.items:
            if item.item_id == item_id:
                return item
        raise KeyError(f"unknown batch item: {item_id}")
