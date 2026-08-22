"""Async Qdrant adapter for the immutable active corpus release."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from src.config import get_settings


@dataclass(frozen=True)
class VectorHit:
    chunk_id: str
    document_id: str
    unit_id: str
    score: float
    input_sha256: str


class QdrantVectorStore:
    """Small read-only boundary around the active Qdrant collection alias."""

    def __init__(self) -> None:
        from qdrant_client import AsyncQdrantClient

        settings = get_settings()
        if not settings.qdrant_url or not settings.qdrant_api_key:
            raise RuntimeError("QDRANT_URL and QDRANT_API_KEY are required")
        self.collection = settings.qdrant_collection
        # Qdrant's REST ``timeout`` query parameter is integral on current
        # Cloud clusters; passing Pydantic's float directly yields ``30.0``
        # and a 400 response.
        self.timeout = max(1, int(settings.qdrant_timeout_seconds))
        self.dimensions = settings.embedding_dimensions
        self.client = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            timeout=self.timeout,
        )

    async def search(
        self,
        vector: Sequence[float],
        *,
        dataset_id: str,
        limit: int,
        document_ids: Sequence[str] | None = None,
        score_threshold: float | None = None,
    ) -> list[VectorHit]:
        """Search only the selected release and answer-ready source passages."""
        if len(vector) != self.dimensions or limit <= 0:
            return []
        from qdrant_client import models

        conditions: list[models.FieldCondition] = [
            models.FieldCondition(key="dataset_id", match=models.MatchValue(value=dataset_id)),
            models.FieldCondition(key="answer_ready", match=models.MatchValue(value=True)),
        ]
        if document_ids:
            conditions.append(
                models.FieldCondition(
                    key="document_id", match=models.MatchAny(any=list(dict.fromkeys(document_ids))),
                )
            )
        response = await self.client.query_points(
            self.collection,
            query=[float(value) for value in vector],
            query_filter=models.Filter(must=conditions),
            limit=limit,
            with_payload=["passage_id", "document_id", "unit_id", "input_sha256"],
            with_vectors=False,
            score_threshold=score_threshold,
            timeout=self.timeout,
        )
        return [
            VectorHit(
                chunk_id=str(point.payload.get("passage_id") or point.id).replace("-", ""),
                document_id=str(point.payload.get("document_id") or ""),
                unit_id=str(point.payload.get("unit_id") or ""),
                score=float(point.score),
                input_sha256=str(point.payload.get("input_sha256") or ""),
            )
            for point in response.points
            if point.payload and point.payload.get("document_id")
        ]

    async def search_many(
        self,
        vectors: Sequence[Sequence[float]],
        *,
        dataset_id: str,
        limit: int,
        document_ids: Sequence[str] | None = None,
        score_threshold: float | None = None,
    ) -> list[list[VectorHit]]:
        """Search bounded sub-query vectors in one Qdrant batch when supported.

        Older Qdrant clients/servers fall back to the same bounded concurrent
        adapter, preserving ordering and release filters without changing
        correctness.
        """
        import asyncio

        values = [list(vector) for vector in vectors]
        if not values:
            return []
        if any(len(vector) != self.dimensions for vector in values) or limit <= 0:
            return [[] for _ in values]
        from qdrant_client import models

        conditions: list[models.FieldCondition] = [
            models.FieldCondition(key="dataset_id", match=models.MatchValue(value=dataset_id)),
            models.FieldCondition(key="answer_ready", match=models.MatchValue(value=True)),
        ]
        if document_ids:
            conditions.append(
                models.FieldCondition(
                    key="document_id", match=models.MatchAny(any=list(dict.fromkeys(document_ids))),
                )
            )
        query_filter = models.Filter(must=conditions)

        async def one(vector: Sequence[float]) -> list[VectorHit]:
            response = await self.client.query_points(
                self.collection,
                query=[float(value) for value in vector],
                query_filter=query_filter,
                limit=limit,
                with_payload=["passage_id", "document_id", "unit_id", "input_sha256"],
                with_vectors=False,
                score_threshold=score_threshold,
                timeout=self.timeout,
            )
            return [
                VectorHit(
                    chunk_id=str(point.payload.get("passage_id") or point.id).replace("-", ""),
                    document_id=str(point.payload.get("document_id") or ""),
                    unit_id=str(point.payload.get("unit_id") or ""),
                    score=float(point.score),
                    input_sha256=str(point.payload.get("input_sha256") or ""),
                )
                for point in response.points
                if point.payload and point.payload.get("document_id")
            ]

        # The client API exposes query_batch only in newer releases. Keep the
        # fallback explicit so dependency upgrades cannot silently widen scope.
        query_batch = getattr(self.client, "query_batch", None)
        if query_batch is None:
            return await asyncio.gather(*(one(vector) for vector in values))
        requests = [
            models.QueryRequest(
                query=vector,
                filter=query_filter,
                limit=limit,
                with_payload=["passage_id", "document_id", "unit_id", "input_sha256"],
                with_vector=False,
                score_threshold=score_threshold,
            )
            for vector in values
        ]
        try:
            responses = await query_batch(collection_name=self.collection, requests=requests)
        except (AttributeError, TypeError, NotImplementedError):
            return await asyncio.gather(*(one(vector) for vector in values))
        return [
            [
                VectorHit(
                    chunk_id=str(point.payload.get("passage_id") or point.id).replace("-", ""),
                    document_id=str(point.payload.get("document_id") or ""),
                    unit_id=str(point.payload.get("unit_id") or ""),
                    score=float(point.score),
                    input_sha256=str(point.payload.get("input_sha256") or ""),
                )
                for point in response.points
                if point.payload and point.payload.get("document_id")
            ]
            for response in responses
        ]

    async def readiness(self, *, dataset_id: str, expected_points: int) -> bool:
        """Validate alias shape and release point count without touching PostgreSQL."""
        from qdrant_client import models

        if not await self.client.collection_exists(self.collection):
            return False
        info = await self.client.get_collection(self.collection)
        vectors = info.config.params.vectors
        if getattr(vectors, "size", None) != self.dimensions:
            return False
        metadata = getattr(info.config, "metadata", None) or {}
        collection_points = int(metadata.get("artifact_rows", 0) or 0)
        required_points = expected_points or collection_points
        if required_points <= 0:
            return False
        count = await self.client.count(
            self.collection,
            count_filter=models.Filter(
                must=[models.FieldCondition(key="dataset_id", match=models.MatchValue(value=dataset_id))]
            ),
            exact=True,
            timeout=self.timeout,
        )
        return count.count == required_points

    async def close(self) -> None:
        await self.client.close()
