from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.integrations.qdrant import QdrantVectorStore


class _Client:
    async def collection_exists(self, _collection: str) -> bool:
        return True

    async def get_collection(self, _collection: str):
        return SimpleNamespace(
            config=SimpleNamespace(
                params=SimpleNamespace(vectors={"dense": SimpleNamespace(size=1536)}),
                metadata={"artifact_rows": 2},
            )
        )

    async def count(self, *_args, **_kwargs):
        return SimpleNamespace(count=2)


class _DiscoveryClient(_Client):
    async def get_collections(self):
        return SimpleNamespace(
            collections=[SimpleNamespace(name="medical_legal_hybrid_snapshot")]
        )

    async def count(self, collection, *_args, **_kwargs):
        return SimpleNamespace(count=2 if collection == "medical_legal_hybrid_snapshot" else 0)


@pytest.mark.asyncio
async def test_readiness_accepts_named_dense_vector_in_hybrid_release() -> None:
    """A hybrid Qdrant alias must be healthy after the dense→named migration."""
    store = object.__new__(QdrantVectorStore)
    store.collection = "medical_legal_active"
    store.dimensions = 1536
    store.timeout = 5
    store.client = _Client()

    assert await store.readiness(dataset_id="snapshot", expected_points=2) is True


@pytest.mark.asyncio
async def test_resolve_collection_recovers_stale_environment_alias() -> None:
    store = object.__new__(QdrantVectorStore)
    store.collection = "medical_legal_active"
    store.dimensions = 1536
    store.timeout = 5
    store._resolved_release = None
    store._hybrid_bm25 = None
    store.client = _DiscoveryClient()

    assert await store.resolve_collection(dataset_id="snapshot", expected_points=2) is True
    assert store.collection == "medical_legal_hybrid_snapshot"
