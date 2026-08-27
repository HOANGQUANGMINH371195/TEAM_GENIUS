"""OpenAI Batch API adapter for an immutable :class:`BatchManifest`.

The adapter is intentionally invoked explicitly by offline jobs only. It never
participates in interactive request handling and never logs credentials or
provider payloads.
"""

from __future__ import annotations

import io
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
