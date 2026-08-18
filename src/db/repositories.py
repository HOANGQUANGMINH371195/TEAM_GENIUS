from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.integrations.neo4j import Neo4jGraphStore
from src.models.graph import DocumentCandidate, Relation, RetrievalResult


class GraphRepository:
    """Read-only retrieval boundary over the active PostgreSQL release."""

    def __init__(self, session: AsyncSession, graph_store: Neo4jGraphStore | None = None):
        self.session = session
        self.graph_store = graph_store

    async def current_dataset(self) -> str | None:
        result = await self.session.execute(
            text(
                """
                SELECT d.dataset_id
                FROM dataset_state state
                JOIN datasets d ON d.dataset_id = state.active_dataset_id
                WHERE state.singleton = TRUE AND d.status = 'active'
                """
            )
        )
        dataset_id = result.scalar_one_or_none()
        return str(dataset_id) if dataset_id is not None else None

    async def current_dataset_release(self) -> tuple[str, int] | None:
        """Return the active release and its expected external-vector count."""
        result = await self.session.execute(
            text(
                """
                SELECT d.dataset_id,
                       COALESCE((d.manifest -> 'counts' ->> 'semantic_passages')::integer, 0)
                           AS semantic_passages
                FROM dataset_state state
                JOIN datasets d ON d.dataset_id = state.active_dataset_id
                WHERE state.singleton = TRUE AND d.status = 'active'
                """
            )
        )
        row = result.one_or_none()
        return (str(row.dataset_id), int(row.semantic_passages)) if row is not None else None

    async def find_documents(self, query: str, *, dataset_id: str | None = None, limit: int = 5) -> list[DocumentCandidate]:
        """Find a legal instrument by number/title without loading a vector model."""
        needle = query.strip()
        if not needle:
            return []
        dataset_id = dataset_id or await self.current_dataset()
        if dataset_id is None:
            return []
        result = await self.session.execute(
            text(
                """
                SELECT d.id, d.title, d.categories, d.payload,
                       COALESCE(d.payload -> 'metadata' ->> 'so_ky_hieu', d.payload ->> 'so_ky_hieu', '') AS so_ky_hieu,
                       COALESCE(d.payload -> 'metadata' ->> 'ngay_ban_hanh', d.payload ->> 'ngay_ban_hanh', '') AS ngay_ban_hanh,
                       COALESCE(d.payload -> 'metadata' ->> 'ngay_co_hieu_luc', d.payload ->> 'ngay_co_hieu_luc', '') AS ngay_co_hieu_luc,
                       COALESCE(d.payload -> 'metadata' ->> 'ngay_het_hieu_luc', d.payload ->> 'ngay_het_hieu_luc', '') AS ngay_het_hieu_luc,
                       COALESCE(d.payload -> 'metadata' ->> 'status_filter', d.payload ->> 'status_filter', '') AS legal_status,
                       COALESCE((d.payload -> 'metadata' ->> 'answer_ready')::boolean, FALSE) AS answer_ready
                FROM documents d
                WHERE d.dataset_id = :dataset_id
                  AND NOT d.is_external
                  AND (
                      COALESCE(d.payload -> 'metadata' ->> 'so_ky_hieu', d.payload ->> 'so_ky_hieu', '') ILIKE :exact_needle
                      OR d.title ILIKE :contains_needle
                  )
                ORDER BY
                    CASE WHEN COALESCE(d.payload -> 'metadata' ->> 'so_ky_hieu', d.payload ->> 'so_ky_hieu', '') ILIKE :exact_needle THEN 0 ELSE 1 END,
                    d.title, d.id
                LIMIT :limit
                """
            ),
            {"dataset_id": dataset_id, "exact_needle": needle, "contains_needle": f"%{needle}%", "limit": limit},
        )
        return [
            DocumentCandidate(
                document_id=str(row.id), title=str(row.title or ""), so_ky_hieu=str(row.so_ky_hieu or ""),
                ngay_ban_hanh=str(row.ngay_ban_hanh or ""), ngay_co_hieu_luc=str(row.ngay_co_hieu_luc or ""),
                ngay_het_hieu_luc=str(row.ngay_het_hieu_luc or ""), legal_status=str(row.legal_status or ""),
                categories=[str(value) for value in (row.categories or [])], answer_ready=bool(row.answer_ready),
            )
            for row in result
        ]

    async def hydrate_chunks(
        self, chunk_ids: Sequence[str], *, dataset_id: str, channel: str = "semantic"
    ) -> list[RetrievalResult]:
        """Hydrate Qdrant/lexical candidates from canonical text in caller order."""
        identifiers = list(dict.fromkeys(str(item) for item in chunk_ids if item))
        if not identifiers:
            return []
        result = await self.session.execute(
            text(
                """
                SELECT candidate.ordinality, c.chunk_id, c.document_id, c.text, c.section_title, c.unit_id,
                       c.source_start, c.source_end, c.text_sha256, c.embedding_input_sha256, d.title
                FROM unnest(CAST(:chunk_ids AS text[])) WITH ORDINALITY AS candidate(chunk_id, ordinality)
                JOIN chunks c ON c.dataset_id = :dataset_id AND c.chunk_id = candidate.chunk_id
                JOIN documents d ON d.dataset_id = c.dataset_id AND d.id = c.document_id
                WHERE c.semantic_eligible IS TRUE OR c.lexical_eligible IS TRUE
                ORDER BY candidate.ordinality
                """
            ),
            {"dataset_id": dataset_id, "chunk_ids": identifiers},
        )
        return [
            RetrievalResult(
                chunk_id=str(row.chunk_id), document_id=str(row.document_id), content=str(row.text or ""),
                source=str(row.document_id), title=str(row.title or ""), section_title=str(row.section_title or ""),
                unit_id=str(row.unit_id or ""), source_start=int(row.source_start) if row.source_start is not None else None,
                source_end=int(row.source_end) if row.source_end is not None else None,
                text_sha256=str(row.text_sha256 or ""), input_sha256=str(row.embedding_input_sha256 or ""), channels=[channel],
            )
            for row in result
        ]

    async def search_lexical(
        self, query: str, *, dataset_id: str, limit: int = 20, document_ids: Sequence[str] | None = None
    ) -> list[RetrievalResult]:
        """Bounded full-text search over answer-ready canonical content."""
        needle = query.strip()
        if not needle:
            return []
        ids = list(dict.fromkeys(document_ids or []))
        result = await self.session.execute(
            text(
                """
                WITH ranked AS (
                    SELECT c.chunk_id, c.document_id, c.text, c.section_title, c.unit_id,
                           c.source_start, c.source_end, c.text_sha256, c.embedding_input_sha256, d.title,
                           ts_rank_cd(c.search_vector, websearch_to_tsquery('simple', :query)) AS score
                    FROM chunks c
                    JOIN documents d ON d.dataset_id = c.dataset_id AND d.id = c.document_id
                    WHERE c.dataset_id = :dataset_id
                      AND c.lexical_eligible IS TRUE
                      AND NOT d.is_external
                      AND COALESCE((d.payload -> 'metadata' ->> 'answer_ready')::boolean, FALSE) IS TRUE
                      AND c.search_vector @@ websearch_to_tsquery('simple', :query)
                      AND (cardinality(CAST(:document_ids AS text[])) = 0
                           OR c.document_id = ANY(CAST(:document_ids AS text[])))
                )
                SELECT * FROM ranked ORDER BY score DESC, document_id, chunk_id LIMIT :limit
                """
            ),
            {"dataset_id": dataset_id, "query": needle, "document_ids": ids, "limit": limit},
        )
        return [
            RetrievalResult(
                chunk_id=str(row.chunk_id), document_id=str(row.document_id), content=str(row.text or ""),
                source=str(row.document_id), title=str(row.title or ""), section_title=str(row.section_title or ""),
                score=float(row.score or 0.0), unit_id=str(row.unit_id or ""),
                source_start=int(row.source_start) if row.source_start is not None else None,
                source_end=int(row.source_end) if row.source_end is not None else None,
                text_sha256=str(row.text_sha256 or ""), input_sha256=str(row.embedding_input_sha256 or ""), channels=["lexical"],
            )
            for row in result
        ]

    async def resolve_legal_units(
        self, labels: Sequence[str], *, dataset_id: str, document_ids: Sequence[str], limit: int = 8
    ) -> list[RetrievalResult]:
        """Resolve a requested Điều/Khoản/Điểm inside an already exact-matched document."""
        needles = [label.strip() for label in labels if label.strip()]
        ids = list(dict.fromkeys(document_ids))
        if not needles or not ids:
            return []
        result = await self.session.execute(
            text(
                """
                SELECT u.unit_id, u.document_id, u.label, u.heading, u.text, u.source_start, u.source_end,
                       u.text_sha256, d.title
                FROM legal_units u
                JOIN documents d ON d.dataset_id = u.dataset_id AND d.id = u.document_id
                WHERE u.dataset_id = :dataset_id
                  AND u.document_id = ANY(CAST(:document_ids AS text[]))
                  AND NOT d.is_external
                  AND COALESCE((d.payload -> 'metadata' ->> 'answer_ready')::boolean, FALSE) IS TRUE
                  AND EXISTS (
                    SELECT 1 FROM unnest(CAST(:labels AS text[])) AS needle
                    WHERE u.label ILIKE '%' || needle || '%' OR u.heading ILIKE '%' || needle || '%'
                  )
                ORDER BY u.document_id, u.source_start NULLS LAST, u.unit_id
                LIMIT :limit
                """
            ),
            {"dataset_id": dataset_id, "document_ids": ids, "labels": needles, "limit": limit},
        )
        return [
            RetrievalResult(
                chunk_id=f"unit:{row.unit_id}", document_id=str(row.document_id), content=str(row.text or ""),
                source=str(row.document_id), title=str(row.title or ""), section_title=str(row.heading or row.label or ""),
                unit_id=str(row.unit_id), source_start=int(row.source_start) if row.source_start is not None else None,
                source_end=int(row.source_end) if row.source_end is not None else None,
                text_sha256=str(row.text_sha256 or ""), channels=["page_index"], score=1.0,
            )
            for row in result
        ]

    async def search_vectors(
        self,
        embedding: Sequence[float],
        *,
        limit: int = 5,
        dataset_id: str | None = None,
        similarity_threshold: float | None = None,
    ) -> list[RetrievalResult]:
        if not embedding:
            return []
        dataset_id = dataset_id or await self.current_dataset()
        if dataset_id is None:
            return []
        threshold = (
            get_settings().semantic_similarity_threshold
            if similarity_threshold is None
            else similarity_threshold
        )
        vector = "[" + ",".join(format(float(value), ".10g") for value in embedding) + "]"
        result = await self.session.execute(
            text(
                """
                SELECT c.chunk_id, c.document_id, c.text, c.section_title,
                       d.title, 1.0 - (c.embedding <=> CAST(:embedding AS extensions.vector)) AS score
                FROM chunks c
                JOIN documents d ON d.dataset_id = c.dataset_id AND d.id = c.document_id
                WHERE c.dataset_id = :dataset_id
                  AND c.embedding IS NOT NULL
                  AND c.semantic_eligible IS TRUE
                  AND c.semantic_eligible
                  AND 1.0 - (c.embedding <=> CAST(:embedding AS extensions.vector)) >= :similarity_threshold
                ORDER BY c.embedding <=> CAST(:embedding AS extensions.vector), c.chunk_id
                LIMIT :limit
                """
            ),
            {
                "embedding": vector,
                "dataset_id": dataset_id,
                "limit": limit,
                "similarity_threshold": threshold,
            },
        )

        return [
            RetrievalResult(
                chunk_id=str(row.chunk_id),
                document_id=str(row.document_id),
                content=str(row.text or ""),
                source=str(row.document_id),
                title=str(row.title or ""),
                section_title=str(row.section_title or ""),
                score=max(0.0, float(row.score)),
                channels=["semantic"],
            )
            for row in result
        ]

    async def hydrate_documents(
        self,
        document_ids: Sequence[str],
        *,
        dataset_id: str | None = None,
        chunks_per_document: int = 2,
    ) -> list[RetrievalResult]:
        if not document_ids:
            return []
        dataset_id = dataset_id or await self.current_dataset()
        if dataset_id is None:
            return []
        result = await self.session.execute(
            text(
                """
                SELECT chunk_id, document_id, text, section_title, title
                FROM (
                    SELECT c.chunk_id, c.document_id, c.text, c.section_title,
                           d.title,
                           ROW_NUMBER() OVER (
                               PARTITION BY c.document_id
                               ORDER BY c.chunk_order, c.chunk_id
                           ) AS chunk_rank
                    FROM chunks c
                    JOIN documents d ON d.dataset_id = c.dataset_id AND d.id = c.document_id
                    WHERE c.dataset_id = :dataset_id AND c.document_id = ANY(:document_ids)
                ) ranked
                WHERE chunk_rank <= :chunks_per_document
                ORDER BY document_id, chunk_rank, chunk_id
                """
            ),
            {
                "dataset_id": dataset_id,
                "document_ids": list(document_ids),
                "chunks_per_document": chunks_per_document,
            },
        )
        return [
            RetrievalResult(
                chunk_id=str(row.chunk_id),
                document_id=str(row.document_id),
                content=str(row.text or ""),
                source=str(row.document_id),
                title=str(row.title or ""),
                section_title=str(row.section_title or ""),
                channels=["legal_graph"],
            )
            for row in result
        ]

    async def expand_entities(
        self,
        entity_names: list[str],
        *,
        dataset_id: str | None = None,
        hops: int = 1,
        limit: int = 20,
    ) -> list[Relation]:
        if not entity_names or self.graph_store is None:
            return []
        dataset_id = dataset_id or await self.current_dataset()
        if dataset_id is None:
            return []
        return await self.graph_store.expand(
            entity_names, dataset_id=dataset_id, hops=hops, limit=limit
        )
