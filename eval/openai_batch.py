"""OpenAI Batch API adapter for an immutable :class:`BatchManifest`.

The adapter is intentionally invoked explicitly by offline jobs only. It never
participates in interactive request handling and never logs credentials or
provider payloads.
"""

from __future__ import annotations

import io
import json
from typing import Any

from eval.batch_manifest import BatchManifest


def provider_jsonl(manifest: BatchManifest, *, endpoint: str = "/v1/responses") -> bytes:
    return manifest.to_provider_jsonl(endpoint=endpoint).encode("utf-8")


async def submit_openai_batch(
    manifest: BatchManifest,
    *,
    client: Any,
    endpoint: str = "/v1/responses",
    completion_window: str = "24h",
) -> str:
    """Upload one manifest and return the provider batch ID."""
    content = provider_jsonl(manifest, endpoint=endpoint)
    if not content.strip():
        raise ValueError("manifest has no pending provider items")
    uploaded = await client.files.create(
        file=io.BytesIO(content),
        purpose="batch",
    )
    batch = await client.batches.create(
        input_file_id=uploaded.id,
        endpoint=endpoint,
        completion_window=completion_window,
        metadata={"manifest_id": manifest.manifest_id, "release_id": manifest.release_id},
    )
    return str(batch.id)


def reconcile_openai_batch_results(
    manifest: BatchManifest,
    results: str | list[dict[str, Any]],
    *,
    max_attempts: int = 3,
) -> dict[str, Any]:
    """Apply a provider output JSONL to the immutable manifest safely.

    OpenAI returns one JSON object per ``custom_id``.  Reconciliation is
    idempotent for completed items, rejects unknown/duplicate IDs, records
    provider errors as retryable/quarantined states, and leaves missing rows
    pending for a later poll.  It never trusts a provider row to change the
    release/model identity stored in the manifest.
    """
    rows = (
        [json.loads(line) for line in results.splitlines() if line.strip()]
        if isinstance(results, str)
        else list(results)
    )
    known = {item.item_id for item in manifest.items}
    seen: set[str] = set()
    invalid: list[dict[str, str]] = []
    applied = 0
    errors = 0
    for row in rows:
        if not isinstance(row, dict):
            invalid.append({"reason": "row_not_object", "custom_id": ""})
            continue
        custom_id = str(row.get("custom_id") or "")
        if not custom_id or custom_id not in known:
            invalid.append({"reason": "unknown_custom_id", "custom_id": custom_id})
            continue
        if custom_id in seen:
            invalid.append({"reason": "duplicate_custom_id", "custom_id": custom_id})
            continue
        seen.add(custom_id)
        error = row.get("error")
        response = row.get("response")
        status_code = int(response.get("status_code", 0)) if isinstance(response, dict) else 0
        if error or status_code >= 400:
            error_class = str((error or {}).get("code") if isinstance(error, dict) else error or f"http_{status_code}")
            manifest.mark_error(custom_id, error_class, max_attempts=max_attempts)
            errors += 1
            continue
        item = next(item for item in manifest.items if item.item_id == custom_id)
        if item.status == "complete":
            # Idempotent replay: the row is already accounted for.
            continue
        body = response.get("body", {}) if isinstance(response, dict) else {}
        usage = body.get("usage", {}) if isinstance(body, dict) else {}
        output_tokens = int(
            usage.get("output_tokens", usage.get("completion_tokens", 0))
            if isinstance(usage, dict)
            else 0
        )
        actual_cost = row.get("actual_cost_usd")
        manifest.mark_result(
            custom_id,
            output_tokens=output_tokens,
            actual_cost_usd=float(actual_cost) if actual_cost is not None else None,
        )
        applied += 1
    pending = [item.item_id for item in manifest.items if item.status == "pending"]
    return {
        "manifest_id": manifest.manifest_id,
        "applied": applied,
        "errors": errors,
        "invalid": invalid,
        "pending": pending,
        "complete": sum(item.status == "complete" for item in manifest.items),
        "quarantined": sum(item.status == "quarantined" for item in manifest.items),
    }
