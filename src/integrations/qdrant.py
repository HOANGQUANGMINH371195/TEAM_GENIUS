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
            # BM25 documents are embedded inside Qdrant Cloud.  Keeping this
            # explicit prevents qdrant-client from silently downloading and
            # running FastEmbed in a Render web process.
            cloud_inference=True,
        )
        self._hybrid_bm25: bool | None = None
        self._resolved_release: tuple[str, int] | None = None

    def set_collection(self, collection: str) -> None:
        """Switch to a verified release collection and reset capabilities."""
        value = str(collection or "").strip()
        if value and value != self.collection:
            self.collection = value
            self._hybrid_bm25 = None
            self._resolved_release = None

    async def resolve_collection(
        self,
        *,
        dataset_id: str,
        expected_points: int,
        preferred_collection: str | None = None,
    ) -> bool:
        """Resolve a stale env alias to the immutable collection holding a release.

        PostgreSQL's projection locator is authoritative, but older backfills may
        contain a logical locator while Qdrant retains a release-suffixed physical
        collection.  Discovery is bounded to the collection list and requires an
        exact release point count; no collection is created, renamed or mutated.
        """
        if getattr(self, "_resolved_release", None) == (dataset_id, expected_points):
            return True
        if not dataset_id or expected_points <= 0:
            return False
        from qdrant_client import models

        release_filter = models.Filter(
            must=[models.FieldCondition(key="dataset_id", match=models.MatchValue(value=dataset_id))]
        )
        # Probe the release projection locator first.  This is both faster and
        # safer than enumerating every collection on Qdrant Cloud: PostgreSQL
        # is the release control plane, while collection discovery is only a
        # compatibility fallback for older projection rows.
        preferred = str(preferred_collection or "").strip()
        if preferred and "<" not in preferred:
            try:
                info = await self.client.get_collection(preferred)
                vectors = info.config.params.vectors
                dense_vectors = vectors.get("dense") if isinstance(vectors, dict) else vectors
                if getattr(dense_vectors, "size", None) == self.dimensions:
                    count = await self.client.count(
                        preferred,
                        count_filter=release_filter,
                        exact=True,
                        timeout=self.timeout,
                    )
                    if int(count.count) == expected_points:
                        self.set_collection(preferred)
                        self._resolved_release = (dataset_id, expected_points)
                        return True
            except Exception:
                # A stale logical locator is expected during rolling upgrades;
                # fall through to bounded discovery below.
                pass

        # Lightweight test doubles and older client wrappers may not expose
        # collection discovery. Keep the normal readiness check as the source
        # of truth for those callers.
        if not hasattr(self.client, "get_collections"):
            return True
        try:
            listed = await self.client.get_collections()
            names = [str(item.name) for item in listed.collections]
        except Exception:
            return False
        candidates = list(dict.fromkeys([preferred, self.collection, *names]))
        for candidate in candidates[:32]:
            if not candidate or "<" in candidate:
                continue
            try:
                info = await self.client.get_collection(candidate)
                vectors = info.config.params.vectors
                dense_vectors = vectors.get("dense") if isinstance(vectors, dict) else vectors
                if getattr(dense_vectors, "size", None) != self.dimensions:
                    continue
                count = await self.client.count(
                    candidate, count_filter=release_filter, exact=True, timeout=self.timeout
                )
                if int(count.count) == expected_points:
                    self.set_collection(candidate)
                    self._resolved_release = (dataset_id, expected_points)
                    return True
            except Exception:
                continue
        return False

    async def _supports_hybrid_bm25(self) -> bool:
        """Detect a fully published hybrid collection once per process.

        A deployment may be upgraded before its new Qdrant release is built.
        In that interval dense retrieval remains correct; BM25 is never
        guessed from collection names or enabled from an environment flag.
        """
        if self._hybrid_bm25 is not None:
            return self._hybrid_bm25
        try:
            info = await self.client.get_collection(self.collection)
            vectors = info.config.params.vectors
            sparse = info.config.params.sparse_vectors or {}
            self._hybrid_bm25 = isinstance(vectors, dict) and {"dense"} <= set(vectors) and "bm25" in sparse
        except Exception:
            self._hybrid_bm25 = False
        return self._hybrid_bm25

    async def search(
        self,
        vector: Sequence[float],
        *,
        query_text: str,
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
        query_filter = models.Filter(must=conditions)
        kwargs = dict(
            collection_name=self.collection,
            query_filter=query_filter,
            limit=limit,
            with_payload=["passage_id", "document_id", "unit_id", "input_sha256"],
            with_vectors=False,
            score_threshold=score_threshold,
            timeout=self.timeout,
        )
        if await self._supports_hybrid_bm25():
            try:
                # RRF scores are rank-based (and intentionally fall below
                # 0.25 after only a few positions). Applying the dense cosine
                # threshold to the *fused* result silently truncated a 60-hit
                # candidate pool to about three hits. Scope it to dense only;
                # sparse BM25 remains an independent recall channel.
                hybrid_kwargs = {key: value for key, value in kwargs.items() if key != "score_threshold"}
                response = await self.client.query_points(
                    prefetch=[
                        models.Prefetch(
                            query=[float(value) for value in vector],
                            using="dense",
                            limit=limit,
                            score_threshold=score_threshold,
                        ),
                        models.Prefetch(
                            query=models.Document(text=query_text, model="qdrant/bm25"),
                            using="bm25",
                            limit=limit,
                        ),
                    ],
                    query=models.FusionQuery(fusion=models.Fusion.RRF),
                    **hybrid_kwargs,
                )
            except Exception:
                # A stale cluster capability must not make legal retrieval
                # unavailable.  The release remains observable as dense-only
                # until its BM25 capability is fixed and republished.
                self._hybrid_bm25 = False
                response = await self.client.query_points(
                    query=[float(value) for value in vector], **kwargs
                )
        else:
            response = await self.client.query_points(query=[float(value) for value in vector], **kwargs)
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
        query_texts: Sequence[str] | None = None,
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
        texts = list(query_texts or [])
        if texts and len(texts) != len(values):
            raise ValueError("query_texts must have the same length as vectors")
        if await self._supports_hybrid_bm25():
            # Qdrant's batch API cannot represent a distinct server-side BM25
            # Document for every request on all supported client versions.
            # The bounded fan-out preserves hybrid semantics for decomposed
            # queries and avoids accidentally addressing a named collection
            # as an old unnamed dense vector collection.
            if not texts:
                raise ValueError("hybrid Qdrant search_many requires query_texts")
            return await asyncio.gather(
                *(
                    self.search(
                        vector,
                        query_text=query_text,
                        dataset_id=dataset_id,
                        limit=limit,
                        document_ids=document_ids,
                        score_threshold=score_threshold,
                    )
                    for vector, query_text in zip(values, texts, strict=True)
                )
            )
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

        if not await self.resolve_collection(dataset_id=dataset_id, expected_points=expected_points):
            return False
        if not await self.client.collection_exists(self.collection):
            return False
        info = await self.client.get_collection(self.collection)
        vectors = info.config.params.vectors
        # A hybrid release declares named vectors (``dense`` + sparse
        # ``bm25``), whereas the legacy release has a single unnamed dense
        # vector. Readiness must validate the dense component in either shape;
        # otherwise a healthy hybrid release would fail /ready immediately
        # after an alias switch.
        dense_vectors = vectors.get("dense") if isinstance(vectors, dict) else vectors
        if getattr(dense_vectors, "size", None) != self.dimensions:
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
