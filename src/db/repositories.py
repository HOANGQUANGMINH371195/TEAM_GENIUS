from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.integrations.neo4j import Neo4jGraphStore
from src.models.graph import DocumentCandidate, Relation, RetrievalResult
from src.services.retrieval import normalize_identifier


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
        needle = normalize_identifier(query)
        compact_needle = needle.replace("-", "").replace("/", "")
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
                      OR regexp_replace(
                          COALESCE(d.payload -> 'metadata' ->> 'so_ky_hieu', d.payload ->> 'so_ky_hieu', ''),
                          '[-/]', '', 'g'
                      ) ILIKE :compact_needle
                      OR d.title ILIKE :contains_needle
                  )
                ORDER BY
                    CASE WHEN COALESCE(d.payload -> 'metadata' ->> 'so_ky_hieu', d.payload ->> 'so_ky_hieu', '') ILIKE :exact_needle
                              OR regexp_replace(COALESCE(d.payload -> 'metadata' ->> 'so_ky_hieu', d.payload ->> 'so_ky_hieu', ''), '[-/]', '', 'g') ILIKE :compact_needle
                         THEN 0 ELSE 1 END,
                    d.title, d.id
                LIMIT :limit
                """
            ),
            {
                "dataset_id": dataset_id, "exact_needle": needle, "compact_needle": compact_needle,
                "contains_needle": f"%{needle}%", "limit": limit,
            },
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
                chunk_id=str(row.chunk_id), document_id=str(row.document_id), dataset_id=dataset_id, content=str(row.text or ""),
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
                chunk_id=str(row.chunk_id), document_id=str(row.document_id), dataset_id=dataset_id, content=str(row.text or ""),
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
        article_needles = [label for label in needles if label.casefold().startswith("điều")]
        # A bare “Khoản 1” occurs in many articles.  When the question also
        # names an article, resolve the article first and derive its children.
        needles = article_needles or needles
        ids = list(dict.fromkeys(document_ids))
        if not needles or not ids:
            return []
        result = await self.session.execute(
            text(
                """
                WITH RECURSIVE matched AS (
                    SELECT u.dataset_id, u.unit_id, u.document_id, u.parent_unit_id, 'target'::text AS page_role
                    FROM legal_units u
                    WHERE u.dataset_id = :dataset_id
                      AND u.document_id = ANY(CAST(:document_ids AS text[]))
                      AND EXISTS (
                        SELECT 1 FROM unnest(CAST(:labels AS text[])) AS needle
                        WHERE u.label ILIKE '%' || needle || '%' OR u.heading ILIKE '%' || needle || '%'
                      )
                ), ancestors AS (
                    SELECT * FROM matched
                    UNION
                    SELECT parent.dataset_id, parent.unit_id, parent.document_id, parent.parent_unit_id, 'ancestor'::text
                    FROM ancestors child
                    JOIN legal_units parent
                      ON parent.dataset_id = child.dataset_id AND parent.unit_id = child.parent_unit_id
                ), scoped AS (
                    SELECT * FROM ancestors
                    UNION
                    SELECT child.dataset_id, child.unit_id, child.document_id, child.parent_unit_id, 'child'::text
                    FROM legal_units child
                    JOIN matched target ON target.dataset_id = child.dataset_id AND target.unit_id = child.parent_unit_id
                )
                SELECT u.unit_id, u.document_id, u.label, u.heading,
                       CASE WHEN scoped.page_role = 'ancestor'
                            THEN COALESCE(NULLIF(u.text, ''), u.heading, u.label)
                            ELSE COALESCE(
                                NULLIF(u.text, ''),
                                NULLIF(substring(d.content_text from u.source_start + 1 for u.source_end - u.source_start), ''),
                                u.heading, u.label
                            )
                       END AS text,
                       u.source_start, u.source_end,
                       u.text_sha256, d.title, scoped.page_role
                FROM scoped
                JOIN legal_units u ON u.dataset_id = scoped.dataset_id AND u.unit_id = scoped.unit_id
                JOIN documents d ON d.dataset_id = u.dataset_id AND d.id = u.document_id
                WHERE NOT d.is_external
                  AND COALESCE((d.payload -> 'metadata' ->> 'answer_ready')::boolean, FALSE) IS TRUE
                ORDER BY CASE scoped.page_role WHEN 'target' THEN 0 WHEN 'child' THEN 1 ELSE 2 END,
                         u.document_id, u.source_start NULLS LAST, u.unit_id
                LIMIT :limit
                """
            ),
            {"dataset_id": dataset_id, "document_ids": ids, "labels": needles, "limit": limit},
        )
        return [
            RetrievalResult(
                chunk_id=f"unit:{row.unit_id}", document_id=str(row.document_id), dataset_id=dataset_id, content=str(row.text or ""),
                source=str(row.document_id), title=str(row.title or ""), section_title=str(row.heading or row.label or ""),
                unit_id=str(row.unit_id), source_start=int(row.source_start) if row.source_start is not None else None,
                source_end=int(row.source_end) if row.source_end is not None else None,
                text_sha256=str(row.text_sha256 or ""), channels=["page_index"],
                score={"target": 1.0, "ancestor": 0.8, "child": 0.65}.get(str(row.page_role), 0.5),
                rank_details={"page_role": {"target": 3.0, "ancestor": 2.0, "child": 1.0}.get(str(row.page_role), 0.0)},
            )
            for row in result
        ]

    async def expand_sibling_legal_units(
        self, unit_ids: Sequence[str], *, dataset_id: str, limit: int = 12
    ) -> list[RetrievalResult]:
        """Return the complete enumerated scope containing a semantic hit.

        A passage matching ``h)`` is often evidence that the user's question
        concerns the complete a)-h) list.  Expanding its siblings is a
        canonical PostgreSQL read, not a model-generated inference.
        """
        ids = list(dict.fromkeys(str(item) for item in unit_ids if item))
        if not ids or limit <= 0:
            return []
        result = await self.session.execute(
            text(
                """
                WITH parents AS (
                    SELECT DISTINCT parent_unit_id
                    FROM legal_units
                    WHERE dataset_id = :dataset_id
                      AND unit_id = ANY(CAST(:unit_ids AS text[]))
                      AND parent_unit_id IS NOT NULL
                )
                SELECT u.unit_id, u.document_id, u.label, u.heading,
                       COALESCE(
                           NULLIF(u.text, ''),
                           NULLIF(substring(d.content_text from u.source_start + 1 for u.source_end - u.source_start), ''),
                           u.heading, u.label
                       ) AS text,
                       u.source_start, u.source_end, u.text_sha256, d.title
                FROM legal_units u
                JOIN parents parent ON parent.parent_unit_id = u.parent_unit_id
                JOIN documents d ON d.dataset_id = u.dataset_id AND d.id = u.document_id
                WHERE u.dataset_id = :dataset_id
                  AND NOT d.is_external
                  AND COALESCE((d.payload -> 'metadata' ->> 'answer_ready')::boolean, FALSE) IS TRUE
                ORDER BY u.document_id, u.source_start NULLS LAST, u.unit_id
                LIMIT :limit
                """
            ),
            {"dataset_id": dataset_id, "unit_ids": ids, "limit": limit},
        )
        return [
            RetrievalResult(
                chunk_id=f"unit:{row.unit_id}", document_id=str(row.document_id), dataset_id=dataset_id,
                content=str(row.text or ""), source=str(row.document_id), title=str(row.title or ""),
                section_title=str(row.heading or row.label or ""), unit_id=str(row.unit_id),
                source_start=int(row.source_start) if row.source_start is not None else None,
                source_end=int(row.source_end) if row.source_end is not None else None,
                text_sha256=str(row.text_sha256 or ""),
                channels=["page_index", "semantic_scope"], score=1.0,
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
