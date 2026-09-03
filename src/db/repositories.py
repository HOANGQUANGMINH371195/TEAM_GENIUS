from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.integrations.neo4j import Neo4jGraphStore
from src.models.graph import DocumentCandidate, Relation, RetrievalResult
from src.services.retrieval import extract_query_phrases, extract_query_terms, normalize_identifier

_LEXICAL_TOKEN = re.compile(r"[0-9A-Za-zÀ-ỹĐđ]+", re.IGNORECASE)


def canonical_embedding_input_sha256(section_title: str, content: str) -> str:
    """Digest the exact embedding input used by the release builder.

    Older staged releases predate the persistence of
    ``chunks.embedding_input_sha256``.  Their canonical section/text remains
    immutable, however, and the embedding artifact has always used this
    exact join.  Reconstructing the digest lets runtime verify the Qdrant
    payload against Postgres instead of dropping every valid dense hit or
    trusting Qdrant without a canonical counterpart.
    """
    value = "\n\n".join(part for part in (section_title, content) if part)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def lexical_phrases(query: str, *, limit: int = 48) -> list[str]:
    """Return bounded query-derived phrases for lexical recall.

    Legal provisions frequently express the decisive exception in a short
    phrase while the user supplies additional facts.  Four-word Vietnamese
    concepts (for example a noun phrase made of two compound words) are common
    enough that only bi-grams/trigrams lose the decisive phrase.  Keeping
    query-derived 2--5 grams avoids that loss without maintaining a domain
    keyword catalogue or turning full-text search into an all-terms filter.
    """
    tokens = [token.casefold() for token in _LEXICAL_TOKEN.findall(query)]
    by_width = [
        list(dict.fromkeys(" ".join(tokens[index : index + width]) for index in range(len(tokens) - width + 1)))
        for width in (5, 4, 3, 2)
    ]
    all_phrases = list(dict.fromkeys(phrase for phrases in by_width for phrase in phrases))
    if len(all_phrases) <= max(0, limit):
        return all_phrases
    # A fixed prefix cut makes long questions systematically lose their
    # decisive ending (for example the service or exception after a broad
    # legal preamble).  Sample every n-gram width across the complete query
    # instead.  This remains fully query-derived and bounded, but preserves
    # both the beginning and end of natural-language questions.
    budget = max(0, limit)
    selected: list[str] = []
    non_empty = [(phrases, 3 if width == 2 else 1) for phrases, width in zip(by_width, (5, 4, 3, 2)) if phrases]
    for group_index, (phrases, weight) in enumerate(non_empty):
        remaining_weight = sum(item_weight for _, item_weight in non_empty[group_index:])
        quota = min(len(phrases), max(1, round(budget * weight / remaining_weight)))
        # Endpoint-inclusive sampling gives each portion of a question a
        # chance to contribute, including its final condition/exception.
        for sample in range(quota):
            index = (
                0
                if quota == 1
                else round(sample * (len(phrases) - 1) / (quota - 1))
            )
            candidate = phrases[index]
            if candidate not in selected:
                selected.append(candidate)
        budget = max(0, limit - len(selected))
    return selected


def lexical_disjunction(query: str, *, limit: int = 32) -> str:
    """Build a safe, bounded OR query from user-supplied lexical tokens."""
    # Use the shared query-term normalizer so function words do not make the
    # disjunction look relevant merely because they occur in nearly every
    # statute.  The terms are still entirely query-derived; no topic-to-answer
    # vocabulary is introduced here.
    terms = extract_query_terms(query, limit=max(0, limit))
    return " | ".join(terms)


def lexical_phrase_disjunction(query: str, *, limit: int = 12) -> str:
    """Build one indexed tsquery from contiguous, user-derived phrases."""
    clauses: list[str] = []
    for phrase in extract_query_phrases(query, limit=max(0, limit)):
        tokens = [token.casefold() for token in _LEXICAL_TOKEN.findall(phrase)]
        if len(tokens) < 2:
            continue
        clauses.append("(" + " <-> ".join(tokens) + ")")
    return " | ".join(dict.fromkeys(clauses))


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
                LEFT JOIN ops.active_release pointer ON pointer.singleton = TRUE
                JOIN datasets d ON d.dataset_id = COALESCE(pointer.active_dataset_id, state.active_dataset_id)
                WHERE state.singleton = TRUE AND d.status = 'active'
                """
            )
        )
        dataset_id = result.scalar_one_or_none()
        return str(dataset_id) if dataset_id is not None else None

    async def public_document_html(
        self, document_number: str, *, dataset_id: str | None = None
    ) -> dict[str, object] | None:
        """Load canonical HTML by public signature from the active release."""
        normalized = normalize_identifier(document_number)
        result = await self.session.execute(
            text(
                """
                SELECT d.id, d.title, d.raw_html, d.raw_html_sha256,
                       COALESCE(d.payload -> 'metadata' ->> 'so_ky_hieu', d.payload ->> 'so_ky_hieu', '') AS document_number,
                       COALESCE(d.payload -> 'metadata' ->> 'ngay_co_hieu_luc', d.payload ->> 'ngay_co_hieu_luc', '') AS effective_from,
                       COALESCE(d.payload -> 'metadata' ->> 'ngay_het_hieu_luc', d.payload ->> 'ngay_het_hieu_luc', '') AS effective_to,
                       COALESCE(d.payload -> 'metadata' ->> 'official_status_url', d.payload ->> 'official_status_url', '') AS source_url
                FROM documents d
                JOIN datasets ds ON ds.dataset_id = d.dataset_id
                WHERE d.dataset_id = COALESCE(:dataset_id, (
                    SELECT COALESCE(pointer.active_dataset_id, state.active_dataset_id)
                    FROM dataset_state state
                    LEFT JOIN ops.active_release pointer ON pointer.singleton = TRUE
                    WHERE state.singleton = TRUE
                ))
                  AND ds.status = 'active'
                  AND upper(replace(replace(COALESCE(d.payload -> 'metadata' ->> 'so_ky_hieu', d.payload ->> 'so_ky_hieu', ''), 'Ð', 'Đ'), 'ð', 'đ')) = :document_number
                  AND d.raw_html <> ''
                LIMIT 1
                """
            ),
            {"dataset_id": dataset_id, "document_number": normalized},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def public_document_metadata(
        self, document_number: str, *, dataset_id: str | None = None
    ) -> dict[str, object] | None:
        """Resolve a public signature to canonical release metadata."""
        normalized = normalize_identifier(document_number)
        result = await self.session.execute(
            text(
                """
                SELECT d.id, d.title,
                       COALESCE(d.payload -> 'metadata' ->> 'so_ky_hieu', d.payload ->> 'so_ky_hieu', '') AS document_number,
                       COALESCE(d.payload -> 'metadata' ->> 'ngay_ban_hanh', d.payload ->> 'ngay_ban_hanh', '') AS issued_at,
                       COALESCE(d.payload -> 'metadata' ->> 'ngay_co_hieu_luc', d.payload ->> 'ngay_co_hieu_luc', '') AS effective_from,
                       COALESCE(d.payload -> 'metadata' ->> 'ngay_het_hieu_luc', d.payload ->> 'ngay_het_hieu_luc', '') AS effective_to,
                       COALESCE(d.payload -> 'metadata' ->> 'tinh_trang_hieu_luc', d.payload ->> 'tinh_trang_hieu_luc', '') AS status,
                       COALESCE(d.payload -> 'metadata' ->> 'official_status_url', d.payload ->> 'official_status_url', '') AS source_url,
                       d.dataset_id
                FROM documents d
                JOIN datasets ds ON ds.dataset_id = d.dataset_id
                WHERE d.dataset_id = COALESCE(:dataset_id, (
                    SELECT COALESCE(pointer.active_dataset_id, state.active_dataset_id)
                    FROM dataset_state state
                    LEFT JOIN ops.active_release pointer ON pointer.singleton = TRUE
                    WHERE state.singleton = TRUE
                ))
                  AND ds.status = 'active'
                  AND upper(replace(replace(COALESCE(d.payload -> 'metadata' ->> 'so_ky_hieu', d.payload ->> 'so_ky_hieu', ''), 'Ð', 'Đ'), 'ð', 'đ')) = :document_number
                  AND NOT d.is_external
                LIMIT 1
                """
            ),
            {"dataset_id": dataset_id, "document_number": normalized},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def public_document_metadata_by_ids(
        self, document_ids: Sequence[str], *, dataset_id: str
    ) -> dict[str, dict[str, object]]:
        """Hydrate graph document IDs in one canonical PostgreSQL read."""
        ids = list(dict.fromkeys(str(value) for value in document_ids if value))[:100]
        if not ids:
            return {}
        result = await self.session.execute(
            text(
                """
                SELECT d.id, d.title,
                       COALESCE(d.payload -> 'metadata' ->> 'so_ky_hieu', d.payload ->> 'so_ky_hieu', '') AS document_number,
                       COALESCE(d.payload -> 'metadata' ->> 'ngay_ban_hanh', d.payload ->> 'ngay_ban_hanh', '') AS issued_at,
                       COALESCE(d.payload -> 'metadata' ->> 'ngay_co_hieu_luc', d.payload ->> 'ngay_co_hieu_luc', '') AS effective_from,
                       COALESCE(d.payload -> 'metadata' ->> 'ngay_het_hieu_luc', d.payload ->> 'ngay_het_hieu_luc', '') AS effective_to,
                       COALESCE(d.payload -> 'metadata' ->> 'tinh_trang_hieu_luc', d.payload ->> 'tinh_trang_hieu_luc', '') AS status,
                       COALESCE(d.payload -> 'metadata' ->> 'official_status_url', d.payload ->> 'official_status_url', '') AS source_url,
                       d.dataset_id
                FROM documents d
                JOIN datasets ds ON ds.dataset_id = d.dataset_id
                WHERE d.dataset_id = :dataset_id
                  AND d.id = ANY(CAST(:document_ids AS text[]))
                  AND ds.status = 'active'
                  AND NOT d.is_external
                """
            ),
            {"dataset_id": dataset_id, "document_ids": ids},
        )
        return {str(row["id"]): dict(row) for row in result.mappings()}

    async def search_title_documents(
        self, query: str, *, dataset_id: str, limit: int = 4
    ) -> list[str]:
        """Find a small source-authority candidate set from query-derived titles.

        This is not evidence and never emits a document by itself.  It lets a
        later, document-bounded passage scan find an operative clause whose
        wording differs from the user's symptoms or administrative phrasing.
        """
        # Titles are short and this runs over a bounded document metadata
        # index, so retain the complete query-derived phrase set.  The normal
        # corpus-wide passage search remains capped at 48; applying that cap
        # here could omit a decisive formal title phrase occurring late in a
        # HyDE rewrite (for example after the user's circumstances).
        # This is a title-only candidate lookup.  A small set of the longest
        # query-derived phrases is enough to locate formal instrument titles;
        # evaluating every possible n-gram against every title is both
        # redundant and can starve the bounded retrieval deadline when the
        # original and HyDE query run concurrently.
        phrases = lexical_phrases(query, limit=24)
        disjunction = lexical_disjunction(query, limit=64)
        if (not phrases and not disjunction) or limit <= 0:
            return []
        result = await self.session.execute(
            text(
                """
                WITH phrase_queries AS (
                    SELECT phraseto_tsquery('simple', phrase) AS phrase_query,
                           cardinality(regexp_split_to_array(phrase, '\\s+')) AS token_count
                    FROM unnest(CAST(:phrases AS text[])) AS phrase
                )
                SELECT d.id, d.title,
                       max(pq.token_count) AS phrase_length,
                       GREATEST(
                           ts_rank_cd(to_tsvector('simple', d.title), websearch_to_tsquery('simple', :query)),
                           COALESCE(ts_rank_cd(to_tsvector('simple', d.title), to_tsquery('simple', :disjunction)) * 1.5, 0.0),
                           COALESCE(max(ts_rank_cd(to_tsvector('simple', d.title), pq.phrase_query) * pq.token_count * 30.0), 0.0)
                       ) AS score,
                       max(
                           CASE
                               WHEN COALESCE(d.payload -> 'metadata' ->> 'loai_van_ban', '') ILIKE '%luật%'
                                    OR d.title ILIKE 'luật %' THEN 4
                               WHEN COALESCE(d.payload -> 'metadata' ->> 'loai_van_ban', '') ILIKE '%nghị định%'
                                    OR d.title ILIKE 'nghị định %' THEN 3
                               WHEN d.title ILIKE 'văn bản hợp nhất%' THEN 2
                               WHEN d.title ILIKE 'thông tư%' THEN 1
                               ELSE 0
                           END
                       ) AS authority_rank
                FROM documents d
                LEFT JOIN phrase_queries pq
                  ON to_tsvector('simple', d.title) @@ pq.phrase_query
                WHERE d.dataset_id = :dataset_id
                  AND NOT d.is_external
                  AND COALESCE((d.payload -> 'metadata' ->> 'answer_ready')::boolean, FALSE) IS TRUE
                  AND (
                      to_tsvector('simple', d.title) @@ websearch_to_tsquery('simple', :query)
                      OR (:disjunction <> '' AND to_tsvector('simple', d.title) @@ to_tsquery('simple', :disjunction))
                      OR pq.phrase_query IS NOT NULL
                  )
                GROUP BY d.id, d.title, d.payload
                ORDER BY score DESC, authority_rank DESC, phrase_length DESC, d.id
                LIMIT :limit
                """
            ),
            {
                "dataset_id": dataset_id,
                "phrases": phrases,
                "query": query,
                "disjunction": disjunction,
                "limit": limit,
            },
        )
        return [str(row.id) for row in result]

    async def current_authority_document_ids(
        self, query: str = "", *, dataset_id: str, limit: int = 16
    ) -> list[str]:
        """Return query-ranked current authorities for an open legal query.

        The old implementation selected documents by title prefixes and then
        ordered them mostly by publication date.  That made a large, unrelated
        set of recent provincial instruments displace the governing national
        law.  Authority is now derived from the release metadata/status and
        the document's indexed content; the result remains candidate-only and
        must still produce a canonical passage before it can reach an answer.
        """
        query_text = " ".join(str(query or "").split())
        if not query_text or not dataset_id or limit <= 0:
            return []
        result = await self.session.execute(
            text(
                """
                WITH request AS (
                    SELECT websearch_to_tsquery('simple', :query) AS ts_query
                ), candidates AS (
                    SELECT d.id,
                           GREATEST(
                               ts_rank_cd(d.document_search_vector, request.ts_query),
                               ts_rank_cd(
                                   to_tsvector(
                                       'simple',
                                       concat_ws(
                                           ' ', d.title,
                                           d.payload -> 'metadata' ->> 'linh_vuc',
                                           d.payload -> 'metadata' ->> 'nganh',
                                           d.payload -> 'metadata' ->> 'agent_category',
                                           d.payload -> 'metadata' ->> 'loai_van_ban',
                                           d.payload -> 'metadata' ->> 'co_quan_ban_hanh'
                                       )
                                   ),
                                   request.ts_query
                               )
                           ) AS relevance,
                           CASE
                               WHEN (
                                   COALESCE(d.payload -> 'metadata' ->> 'legal_status_verified', d.payload ->> 'legal_status_verified', 'false')::boolean IS TRUE
                                   OR COALESCE(d.payload -> 'metadata' ->> 'metadata_provenance', d.payload ->> 'metadata_provenance', '') IN ('curated_csv', 'official_vbpl')
                               )
                                AND COALESCE(d.payload -> 'metadata' ->> 'tinh_trang_hieu_luc', d.payload -> 'metadata' ->> 'status_filter', d.payload ->> 'tinh_trang_hieu_luc', '') ILIKE 'còn hiệu lực%'
                               THEN 1 ELSE 0
                           END AS verified_current,
                           CASE
                               WHEN d.payload -> 'metadata' ->> 'retrieval_scope' = 'seed_core' THEN 1 ELSE 0
                           END AS release_seed,
                           CASE
                               -- Keep the instrument hierarchy data-derived:
                               -- primary law/decree/unified text outranks local
                               -- administrative reproductions when relevance is
                               -- otherwise comparable.
                               WHEN lower(COALESCE(d.payload -> 'metadata' ->> 'loai_van_ban', '')) LIKE '%luật%' THEN 4
                               WHEN lower(COALESCE(d.payload -> 'metadata' ->> 'loai_van_ban', '')) LIKE '%nghị định%' THEN 3
                               WHEN lower(COALESCE(d.payload -> 'metadata' ->> 'loai_van_ban', '')) LIKE '%hợp nhất%' THEN 2
                               ELSE 1
                           END AS authority_rank,
                           GREATEST(
                               CASE
                                   WHEN COALESCE(d.payload -> 'metadata' ->> 'ngay_co_hieu_luc', '') ~ '\\d{4}$'
                                   THEN RIGHT(d.payload -> 'metadata' ->> 'ngay_co_hieu_luc', 4)::int
                                   ELSE 0
                               END,
                               CASE
                                   WHEN COALESCE(d.payload -> 'metadata' ->> 'ngay_ban_hanh', '') ~ '\\d{4}$'
                                   THEN RIGHT(d.payload -> 'metadata' ->> 'ngay_ban_hanh', 4)::int
                                   ELSE 0
                               END
                           ) AS publication_year
                    FROM documents d
                    CROSS JOIN request
                    WHERE d.dataset_id = :dataset_id
                      AND NOT d.is_external
                      AND COALESCE((d.payload -> 'metadata' ->> 'answer_ready')::boolean, FALSE) IS TRUE
                      AND (
                          (
                              (
                                  COALESCE(d.payload -> 'metadata' ->> 'legal_status_verified', d.payload ->> 'legal_status_verified', 'false')::boolean IS TRUE
                                  OR COALESCE(d.payload -> 'metadata' ->> 'metadata_provenance', d.payload ->> 'metadata_provenance', '') IN ('curated_csv', 'official_vbpl')
                              )
                              AND COALESCE(d.payload -> 'metadata' ->> 'tinh_trang_hieu_luc', d.payload -> 'metadata' ->> 'status_filter', d.payload ->> 'tinh_trang_hieu_luc', '') ILIKE 'còn hiệu lực%'
                          )
                          OR GREATEST(
                              CASE
                                  WHEN COALESCE(d.payload -> 'metadata' ->> 'ngay_co_hieu_luc', '') ~ '\\d{4}$'
                                  THEN RIGHT(d.payload -> 'metadata' ->> 'ngay_co_hieu_luc', 4)::int
                                  ELSE 0
                              END,
                              CASE
                                  WHEN COALESCE(d.payload -> 'metadata' ->> 'ngay_ban_hanh', '') ~ '\\d{4}$'
                                  THEN RIGHT(d.payload -> 'metadata' ->> 'ngay_ban_hanh', 4)::int
                                  ELSE 0
                              END
                          ) >= :minimum_year
                      )
                )
                SELECT id
                FROM candidates
                ORDER BY
                  authority_rank DESC,
                  verified_current DESC,
                  (relevance > 0) DESC,
                  relevance DESC,
                  release_seed DESC,
                  publication_year DESC,
                  id
                LIMIT :limit
                """
            ),
            {
                "dataset_id": dataset_id,
                "query": query_text,
                "limit": max(1, min(limit, 32)),
                "minimum_year": date.today().year - 2,
            },
        )
        return [str(row.id) for row in result]

    async def current_dataset_release(self) -> tuple[str, int] | None:
        """Return the active release and its expected external-vector count."""
        result = await self.session.execute(
            text(
                """
                SELECT d.dataset_id,
                       COALESCE(
                           (SELECT p.expected_count
                            FROM release_projections p
                            WHERE p.dataset_id = d.dataset_id
                              AND p.projection_kind = 'qdrant'
                              AND p.status = 'ready'),
                           (d.manifest -> 'counts' ->> 'semantic_passages')::integer,
                           0
                       ) AS semantic_passages
                FROM dataset_state state
                LEFT JOIN ops.active_release pointer ON pointer.singleton = TRUE
                JOIN datasets d ON d.dataset_id = COALESCE(pointer.active_dataset_id, state.active_dataset_id)
                WHERE state.singleton = TRUE AND d.status = 'active'
                """
            )
        )
        row = result.one_or_none()
        return (str(row.dataset_id), int(row.semantic_passages)) if row is not None else None

    async def search_legal_fact_subjects(
        self, terms: Sequence[str], *, dataset_id: str, limit: int = 8
    ) -> list[str]:
        """Find accepted typed-fact subjects using only query-derived terms."""
        needles = list(dict.fromkeys(str(term).strip() for term in terms if str(term).strip()))[:24]
        if not needles or not dataset_id:
            return []
        result = await self.session.execute(
            text(
                """
                SELECT DISTINCT subject
                FROM legal_facts
                WHERE dataset_id = :dataset_id
                  AND review_status = 'accepted'
                  AND EXISTS (
                      SELECT 1 FROM unnest(CAST(:terms AS text[])) AS needle
                      WHERE lower(subject) LIKE '%' || lower(needle) || '%'
                  )
                ORDER BY subject
                LIMIT :limit
                """
            ),
            {"dataset_id": dataset_id, "terms": needles, "limit": max(1, min(int(limit), 24))},
        )
        return [str(row.subject) for row in result]

    async def hydrate_units_by_ids(
        self, unit_ids: Sequence[str], *, dataset_id: str, limit: int = 12
    ) -> list[RetrievalResult]:
        """Hydrate typed-fact anchors back to canonical PostgreSQL text."""
        ids = list(dict.fromkeys(str(unit_id) for unit_id in unit_ids if str(unit_id)))[:50]
        if not ids or not dataset_id:
            return []
        result = await self.session.execute(
            text(
                """
                SELECT u.unit_id, u.document_id, u.heading, u.text,
                       u.source_start, u.source_end, u.text_sha256, d.title
                FROM legal_units u
                JOIN documents d ON d.dataset_id = u.dataset_id AND d.id = u.document_id
                WHERE u.dataset_id = :dataset_id
                  AND u.unit_id = ANY(CAST(:unit_ids AS text[]))
                  AND NOT d.is_external
                  AND COALESCE((d.payload -> 'metadata' ->> 'answer_ready')::boolean, FALSE) IS TRUE
                ORDER BY u.source_start NULLS LAST, u.unit_id
                LIMIT :limit
                """
            ),
            {"dataset_id": dataset_id, "unit_ids": ids, "limit": max(1, min(int(limit), 50))},
        )
        return [
            RetrievalResult(
                chunk_id=f"unit:{row.unit_id}",
                document_id=str(row.document_id),
                dataset_id=dataset_id,
                content=str(row.text or row.heading or ""),
                source=str(row.document_id),
                title=str(row.title or ""),
                section_title=str(row.heading or ""),
                unit_id=str(row.unit_id),
                source_start=int(row.source_start) if row.source_start is not None else None,
                source_end=int(row.source_end) if row.source_end is not None else None,
                text_sha256=str(row.text_sha256 or ""),
                channels=["typed_fact"],
                score=1.0,
            )
            for row in result
        ]

    async def current_projection_contract(self, dataset_id: str) -> dict[str, dict[str, object]]:
        """Return release-scoped projection rows for readiness/parity checks."""
        result = await self.session.execute(
            text(
                """
                SELECT projection_kind, locator, status, release_fingerprint,
                       expected_count, actual_count, metadata
                FROM release_projections
                WHERE dataset_id = :dataset_id
                ORDER BY projection_kind
                """
            ),
            {"dataset_id": dataset_id},
        )
        return {
            str(row.projection_kind): {
                "locator": str(row.locator),
                "status": str(row.status),
                "release_fingerprint": str(row.release_fingerprint),
                "expected_count": int(row.expected_count),
                "actual_count": int(row.actual_count) if row.actual_count is not None else None,
                "metadata": dict(row.metadata or {}),
            }
            for row in result
        }

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
                       COALESCE(d.payload -> 'metadata' ->> 'official_status_url', d.payload ->> 'official_status_url', '') AS legal_status_source,
                       COALESCE(d.payload -> 'metadata' ->> 'status_checked_at', d.payload ->> 'status_checked_at', '') AS legal_status_checked_at,
                       (
                           COALESCE(d.payload -> 'metadata' ->> 'status_checked_at', d.payload ->> 'status_checked_at', '') <> ''
                           AND (
                               COALESCE(d.payload -> 'metadata' ->> 'official_status_url', d.payload ->> 'official_status_url', '') <> ''
                               OR COALESCE(d.payload -> 'metadata' ->> 'metadata_provenance', d.payload ->> 'metadata_provenance', '') IN ('curated_csv', 'official_vbpl')
                           )
                       ) AS legal_status_verified,
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
                legal_status_verified=bool(row.legal_status_verified),
                legal_status_source=str(row.legal_status_source or ""),
                legal_status_checked_at=str(row.legal_status_checked_at or ""),
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
                       c.source_start, c.source_end, c.text_sha256, c.embedding_input_sha256, d.title,
                       COALESCE(d.payload -> 'metadata' ->> 'so_ky_hieu', d.payload ->> 'so_ky_hieu', '') AS document_number,
                       COALESCE(d.payload -> 'metadata' ->> 'loai_van_ban', d.payload ->> 'loai_van_ban', '') AS document_type,
                       COALESCE(d.payload -> 'metadata' ->> 'ngay_ban_hanh', d.payload ->> 'ngay_ban_hanh', '') AS issued_date,
                       COALESCE(d.payload -> 'metadata' ->> 'ngay_co_hieu_luc', d.payload ->> 'ngay_co_hieu_luc', '') AS effective_from,
                       COALESCE(d.payload -> 'metadata' ->> 'ngay_het_hieu_luc', d.payload ->> 'ngay_het_hieu_luc', '') AS effective_to,
                       COALESCE(d.payload -> 'metadata' ->> 'status_filter', d.payload ->> 'status_filter', '') AS legal_status,
                       COALESCE(d.payload -> 'metadata' ->> 'co_quan_ban_hanh', d.payload ->> 'co_quan_ban_hanh', '') AS issuer,
                       COALESCE(d.payload -> 'metadata' ->> 'pham_vi', d.payload ->> 'pham_vi', '') AS jurisdiction,
                       d.categories,
                       COALESCE(d.payload -> 'metadata' ->> 'official_status_url', d.payload ->> 'official_status_url', '') AS source_url,
                       COALESCE(d.payload -> 'metadata' ->> 'status_checked_at', d.payload ->> 'status_checked_at', '') AS source_checked_at,
                       (
                           COALESCE(d.payload -> 'metadata' ->> 'legal_status_verified', d.payload ->> 'legal_status_verified', 'false')::boolean IS TRUE
                           OR (
                               COALESCE(d.payload -> 'metadata' ->> 'status_checked_at', d.payload ->> 'status_checked_at', '') <> ''
                               AND (
                                   COALESCE(d.payload -> 'metadata' ->> 'official_status_url', d.payload ->> 'official_status_url', '') <> ''
                                   OR COALESCE(d.payload -> 'metadata' ->> 'metadata_provenance', d.payload ->> 'metadata_provenance', '') IN ('curated_csv', 'official_vbpl')
                               )
                           )
                       ) AS legal_status_verified
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
                document_number=str(row.document_number or ""), document_type=str(row.document_type or ""),
                issued_date=str(row.issued_date or ""), effective_from=str(row.effective_from or ""),
                effective_to=str(row.effective_to or ""), legal_status=str(row.legal_status or ""),
                legal_status_verified=bool(row.legal_status_verified), issuer=str(row.issuer or ""),
                jurisdiction=str(row.jurisdiction or ""), categories=[str(value) for value in (row.categories or [])],
                source_url=str(row.source_url or ""), source_checked_at=str(row.source_checked_at or ""),
                unit_id=str(row.unit_id or ""), source_start=int(row.source_start) if row.source_start is not None else None,
                source_end=int(row.source_end) if row.source_end is not None else None,
                text_sha256=str(row.text_sha256 or ""),
                input_sha256=(
                    str(row.embedding_input_sha256 or "")
                    or canonical_embedding_input_sha256(str(row.section_title or ""), str(row.text or ""))
                ),
                channels=[channel],
            )
            for row in result
        ]

    async def search_table_facts(
        self, query: str, *, dataset_id: str, limit: int = 12
    ) -> list[RetrievalResult]:
        """Recall typed table facts and anchor them to canonical legal units."""
        # Avoid tokenizing the entire historical table projection when this
        # release has no reviewed facts.  The partial index is installed by
        # the migration below; the capability guard keeps older databases
        # compatible while they roll forward.
        accepted_check = await self.session.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM table_cell_facts
                    WHERE dataset_id = :dataset_id
                      AND payload ->> 'review_status' = 'accepted'
                )
                """
            ),
            {"dataset_id": dataset_id},
        )
        scalar = getattr(accepted_check, "scalar", None)
        if callable(scalar) and not bool(scalar()):
            return []
        result = await self.session.execute(
            text(
                """
                SELECT f.fact_id, f.subject, f.attribute, f.value, f.document_id,
                       f.legal_unit_id, f.source_fragment_sha256, d.title,
                       COALESCE(d.payload -> 'metadata' ->> 'so_ky_hieu', d.payload ->> 'so_ky_hieu', '') AS document_number,
                       COALESCE(u.heading, u.label, '') AS section_title,
                       u.text AS legal_unit_text,
                       u.source_start, u.source_end, u.text_sha256
                FROM table_cell_facts f
                JOIN documents d ON d.dataset_id = f.dataset_id AND d.id = f.document_id
                JOIN legal_units u ON u.dataset_id = f.dataset_id AND u.unit_id = f.legal_unit_id
                WHERE f.dataset_id = :dataset_id
                  AND f.payload ->> 'review_status' = 'accepted'
                  AND u.text <> ''
                  AND u.text_sha256 <> ''
                  AND to_tsvector('simple', f.subject || ' ' || f.attribute || ' ' || f.value)
                      @@ to_tsquery('simple', :disjunction)
                ORDER BY f.fact_id
                LIMIT :limit
                """
            ),
            {
                "dataset_id": dataset_id,
                "query": query,
                "disjunction": lexical_disjunction(query, limit=24),
                "limit": max(1, min(limit, 50)),
            },
        )
        return [
            RetrievalResult(
                chunk_id=f"table-fact:{row.fact_id}",
                document_id=str(row.document_id or ""),
                dataset_id=dataset_id,
                # The legal unit is the canonical text/hash pair.  The typed
                # fact remains visible as a compact section label, while the
                # content itself is never synthetic (otherwise provenance
                # verification would correctly reject the result).
                content=str(row.legal_unit_text or ""),
                source=str(row.document_id or ""),
                title=str(row.title or ""),
                document_number=str(row.document_number or ""),
                section_title=(
                    f"{row.section_title or ''} — {row.subject}: "
                    f"{row.attribute} = {row.value}"
                ).strip(" —"),
                unit_id=str(row.legal_unit_id or ""),
                source_start=int(row.source_start) if row.source_start is not None else None,
                source_end=int(row.source_end) if row.source_end is not None else None,
                text_sha256=str(row.text_sha256 or row.source_fragment_sha256 or ""),
                channels=["table_fact"],
            )
            for row in result
        ]

    async def document_ranking_metadata(
        self, document_ids: Sequence[str], *, dataset_id: str
    ) -> dict[str, dict[str, object]]:
        """Load verified legal-ranking features without exposing them publicly."""
        identifiers = list(dict.fromkeys(str(item) for item in document_ids if item))
        if not identifiers:
            return {}
        result = await self.session.execute(
            text(
                """
                SELECT d.id, d.title,
                       COALESCE(d.payload -> 'metadata' ->> 'so_ky_hieu', d.payload ->> 'so_ky_hieu', '') AS document_number,
                       COALESCE(d.payload -> 'metadata' ->> 'loai_van_ban', d.payload ->> 'loai_van_ban', '') AS document_type,
                       COALESCE(d.payload -> 'metadata' ->> 'ngay_ban_hanh', d.payload ->> 'ngay_ban_hanh', '') AS issued_date,
                       COALESCE(d.payload -> 'metadata' ->> 'ngay_co_hieu_luc', d.payload ->> 'ngay_co_hieu_luc', '') AS effective_from,
                       COALESCE(d.payload -> 'metadata' ->> 'ngay_het_hieu_luc', d.payload ->> 'ngay_het_hieu_luc', '') AS effective_to,
                       COALESCE(d.payload -> 'metadata' ->> 'status_filter', d.payload ->> 'status_filter', '') AS legal_status,
                       COALESCE(d.payload -> 'metadata' ->> 'co_quan_ban_hanh', d.payload ->> 'co_quan_ban_hanh', '') AS issuer,
                       COALESCE(d.payload -> 'metadata' ->> 'pham_vi', d.payload ->> 'pham_vi', '') AS jurisdiction,
                       d.categories AS categories,
                       COALESCE(d.payload -> 'metadata' ->> 'official_status_url', d.payload ->> 'official_status_url', '') AS source_url,
                       COALESCE(d.payload -> 'metadata' ->> 'status_checked_at', d.payload ->> 'status_checked_at', '') AS source_checked_at,
                       (
                           COALESCE(d.payload -> 'metadata' ->> 'legal_status_verified', d.payload ->> 'legal_status_verified', 'false')::boolean IS TRUE
                           OR (
                               COALESCE(d.payload -> 'metadata' ->> 'status_checked_at', d.payload ->> 'status_checked_at', '') <> ''
                               AND (
                                   COALESCE(d.payload -> 'metadata' ->> 'official_status_url', d.payload ->> 'official_status_url', '') <> ''
                                   OR COALESCE(d.payload -> 'metadata' ->> 'metadata_provenance', d.payload ->> 'metadata_provenance', '') IN ('curated_csv', 'official_vbpl')
                               )
                           )
                       ) AS legal_status_verified
                FROM documents d
                WHERE d.dataset_id = :dataset_id
                  AND d.id = ANY(CAST(:document_ids AS text[]))
                """
            ),
            {"dataset_id": dataset_id, "document_ids": identifiers},
        )
        return {
            str(row.id): {
                "title": str(row.title or ""),
                "document_number": str(row.document_number or ""),
                "document_type": str(row.document_type or ""),
                "issued_date": str(row.issued_date or ""),
                "effective_from": str(row.effective_from or ""),
                "effective_to": str(row.effective_to or ""),
                "legal_status": str(row.legal_status or ""),
                "legal_status_verified": bool(row.legal_status_verified),
                "issuer": str(row.issuer or ""),
                "jurisdiction": str(row.jurisdiction or ""),
                "categories": [str(value) for value in (row.categories or [])],
                "source_url": str(row.source_url or ""),
                "source_checked_at": str(row.source_checked_at or ""),
            }
            for row in result
        }

    async def hydrate_chunks_with_scope(
        self,
        chunk_ids: Sequence[str],
        *,
        dataset_id: str,
        scope_limit: int = 12,
        scope_seed_limit: int = 6,
        channel: str = "semantic",
    ) -> tuple[list[RetrievalResult], list[RetrievalResult]]:
        """Hydrate semantic hits and enumerate sibling units in one DB round trip.

        The CTE preserves Qdrant rank order, then expands only legal-unit hits
        whose section starts with an enumerator (``a)``, ``b)``...).  This keeps
        the scope rule canonical while avoiding a second hydration query before
        the sibling lookup.
        """
        identifiers = list(dict.fromkeys(str(item) for item in chunk_ids if item))
        if not identifiers:
            return [], []
        result = await self.session.execute(
            text(
                """
                WITH hydrated AS (
                    SELECT candidate.ordinality, c.chunk_id, c.document_id, c.text,
                           c.section_title, c.unit_id, c.source_start, c.source_end,
                           c.text_sha256, c.embedding_input_sha256, d.title
                    FROM unnest(CAST(:chunk_ids AS text[])) WITH ORDINALITY AS candidate(chunk_id, ordinality)
                    JOIN chunks c ON c.dataset_id = :dataset_id AND c.chunk_id = candidate.chunk_id
                    JOIN documents d ON d.dataset_id = c.dataset_id AND d.id = c.document_id
                    WHERE c.semantic_eligible IS TRUE OR c.lexical_eligible IS TRUE
                ),
                scope_roots AS (
                    SELECT CASE
                               WHEN h.section_title ~ '^[[:space:]]*[0-9]+\\.' THEN u.unit_id
                               ELSE u.parent_unit_id
                           END AS root_unit_id,
                           min(h.ordinality) AS seed_rank
                    FROM hydrated h
                    JOIN legal_units u
                      ON u.dataset_id = :dataset_id AND u.unit_id = h.unit_id
                    WHERE h.section_title ~ '^[[:space:]]*(?:[a-zđ]\\)|[0-9]+\\.)'
                      AND (
                          h.section_title ~ '^[[:space:]]*[0-9]+\\.'
                          OR u.parent_unit_id IS NOT NULL
                      )
                    GROUP BY root_unit_id
                    ORDER BY seed_rank
                    LIMIT :scope_seed_limit
                ),
                scoped AS (
                    SELECT u.unit_id, u.document_id, u.label, u.heading,
                           COALESCE(
                               NULLIF(u.text, ''),
                               NULLIF(substring(d.content_text from u.source_start + 1 for u.source_end - u.source_start), ''),
                               u.heading, u.label
                           ) AS text,
                           u.source_start, u.source_end, u.text_sha256, d.title,
                           row_number() OVER (ORDER BY p.seed_rank, u.source_start NULLS LAST, u.unit_id) AS scope_ordinal
                    FROM legal_units u
                    JOIN scope_roots p ON p.root_unit_id = u.parent_unit_id
                    JOIN documents d ON d.dataset_id = u.dataset_id AND d.id = u.document_id
                    WHERE u.dataset_id = :dataset_id
                      AND NOT d.is_external
                      AND COALESCE((d.payload -> 'metadata' ->> 'answer_ready')::boolean, FALSE) IS TRUE
                )
                SELECT 'hydrated' AS row_kind, h.ordinality AS row_order,
                       h.chunk_id, h.document_id, h.text, h.section_title, h.unit_id,
                       h.source_start, h.source_end, h.text_sha256,
                       h.embedding_input_sha256, h.title
                FROM hydrated h
                UNION ALL
                SELECT 'scope' AS row_kind, 1000000 + s.scope_ordinal AS row_order,
                       ('unit:' || s.unit_id) AS chunk_id, s.document_id, s.text,
                       COALESCE(s.heading, s.label) AS section_title, s.unit_id,
                       s.source_start, s.source_end, s.text_sha256, '' AS embedding_input_sha256,
                       s.title
                FROM scoped s
                WHERE s.scope_ordinal <= :scope_limit
                ORDER BY row_order
                """
            ),
            {
                "dataset_id": dataset_id,
                "chunk_ids": identifiers,
                "scope_limit": max(0, scope_limit),
                "scope_seed_limit": max(1, scope_seed_limit),
            },
        )
        hydrated: list[RetrievalResult] = []
        scope: list[RetrievalResult] = []
        for row in result:
            if str(row.row_kind) == "hydrated":
                hydrated.append(
                    RetrievalResult(
                        chunk_id=str(row.chunk_id), document_id=str(row.document_id), dataset_id=dataset_id,
                        content=str(row.text or ""), source=str(row.document_id), title=str(row.title or ""),
                        section_title=str(row.section_title or ""), unit_id=str(row.unit_id or ""),
                        source_start=int(row.source_start) if row.source_start is not None else None,
                        source_end=int(row.source_end) if row.source_end is not None else None,
                        text_sha256=str(row.text_sha256 or ""),
                        input_sha256=(
                            str(row.embedding_input_sha256 or "")
                            or canonical_embedding_input_sha256(
                                str(row.section_title or ""), str(row.text or "")
                            )
                        ),
                        channels=[channel],
                    )
                )
            else:
                scope.append(
                    RetrievalResult(
                        chunk_id=str(row.chunk_id), document_id=str(row.document_id), dataset_id=dataset_id,
                        content=str(row.text or ""), source=str(row.document_id), title=str(row.title or ""),
                        section_title=str(row.section_title or ""), unit_id=str(row.unit_id or ""),
                        source_start=int(row.source_start) if row.source_start is not None else None,
                        source_end=int(row.source_end) if row.source_end is not None else None,
                        text_sha256=str(row.text_sha256 or ""), channels=["page_index", "semantic_scope"], score=1.0,
                    )
                )
        return hydrated, scope

    async def search_lexical(
        self, query: str, *, dataset_id: str, limit: int = 20,
        document_ids: Sequence[str] | None = None, include_local: bool = True
    ) -> list[RetrievalResult]:
        """Bounded full-text search over answer-ready canonical content."""
        needle = query.strip()
        if not needle:
            return []
        ids = list(dict.fromkeys(document_ids or []))
        disjunction = lexical_disjunction(needle)
        # Let PostgreSQL cancel a pathological full-text scan server-side so
        # asyncpg does not receive a client-side coroutine cancellation that
        # leaves the pooled connection in an aborted transaction.
        await self.session.execute(text("SET LOCAL statement_timeout = '5000ms'"))
        result = await self.session.execute(
            text(
                """
                WITH candidate_ids AS (
                    -- Bound both full-text branches before ranking.  The old
                    -- OR predicate made PostgreSQL scan every eligible chunk
                    -- and evaluate dozens of phrase subqueries before LIMIT;
                    -- that routinely exceeded the 10s retrieval budget.
                    (SELECT c.chunk_id,
                           2.0 AS seed_score
                    FROM chunks c
                    JOIN documents d ON d.dataset_id = c.dataset_id AND d.id = c.document_id
                    WHERE c.dataset_id = :dataset_id
                      AND c.lexical_eligible IS TRUE
                      AND NOT d.is_external
                      AND COALESCE((d.payload -> 'metadata' ->> 'answer_ready')::boolean, FALSE) IS TRUE
                      AND c.search_vector @@ websearch_to_tsquery('simple', :query)
                      AND (cardinality(CAST(:document_ids AS text[])) = 0
                           OR c.document_id = ANY(CAST(:document_ids AS text[])))
                      AND (:include_local IS TRUE OR NOT (
                           COALESCE(d.payload -> 'metadata' ->> 'pham_vi', d.payload ->> 'pham_vi', '') ~* '(địa phương|tỉnh|thành phố|huyện)'
                           OR d.title ~* 'NQ-HĐND'
                      ))
                    ORDER BY ts_rank_cd(
                                 c.search_vector,
                                 websearch_to_tsquery('simple', :query)
                             ) DESC,
                             c.chunk_id
                    LIMIT 100)
                    UNION ALL
                    (SELECT c.chunk_id,
                           1.0 AS seed_score
                    FROM chunks c
                    JOIN documents d ON d.dataset_id = c.dataset_id AND d.id = c.document_id
                    WHERE c.dataset_id = :dataset_id
                      AND c.lexical_eligible IS TRUE
                      AND NOT d.is_external
                      AND COALESCE((d.payload -> 'metadata' ->> 'answer_ready')::boolean, FALSE) IS TRUE
                      AND :disjunction <> ''
                      AND c.search_vector @@ to_tsquery('simple', :disjunction)
                      AND (cardinality(CAST(:document_ids AS text[])) = 0
                           OR c.document_id = ANY(CAST(:document_ids AS text[])))
                      AND (:include_local IS TRUE OR NOT (
                           COALESCE(d.payload -> 'metadata' ->> 'pham_vi', d.payload ->> 'pham_vi', '') ~* '(địa phương|tỉnh|thành phố|huyện)'
                           OR d.title ~* 'NQ-HĐND'
                      ))
                    ORDER BY ts_rank_cd(
                                 c.search_vector,
                                 to_tsquery('simple', :disjunction)
                             ) DESC,
                             c.chunk_id
                    LIMIT 100)
                ), ranked AS (
                    SELECT c.chunk_id, c.document_id, c.text, c.section_title, c.unit_id,
                           c.source_start, c.source_end, c.text_sha256, c.embedding_input_sha256, d.title,
                           GREATEST(
                               ts_rank_cd(c.search_vector, websearch_to_tsquery('simple', :query)) * 2.0,
                               COALESCE(ts_rank_cd(c.search_vector, to_tsquery('simple', :disjunction)) * 1.5, 0.0),
                               max(ci.seed_score)
                           ) AS score
                    FROM candidate_ids ci
                    JOIN chunks c ON c.dataset_id = :dataset_id AND c.chunk_id = ci.chunk_id
                    JOIN documents d ON d.dataset_id = c.dataset_id AND d.id = c.document_id
                    GROUP BY c.chunk_id, c.document_id, c.text, c.section_title, c.unit_id,
                             c.source_start, c.source_end, c.text_sha256, c.embedding_input_sha256,
                             d.title, c.search_vector
                )
                SELECT * FROM ranked ORDER BY score DESC, document_id, chunk_id LIMIT :limit
                """
            ),
            {
                "dataset_id": dataset_id,
                "query": needle,
                "disjunction": disjunction,
                "document_ids": ids,
                "limit": limit,
                "include_local": include_local,
            },
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

    async def search_lexical_document_ids(
        self, query: str, *, dataset_id: str, limit: int = 64,
        include_local: bool = True,
    ) -> list[str]:
        """Return a bounded, query-derived document recall set.

        This is deliberately document-level: a decisive short provision can
        score below verbose passages in a corpus-wide passage search, while
        still belonging to a highly relevant current law. Callers must fetch
        a matching passage afterwards; document IDs are never evidence.
        """
        disjunction = lexical_disjunction(query)
        phrase_disjunction = lexical_phrase_disjunction(query, limit=12)
        if not disjunction or limit <= 0:
            return []
        # Indexed-only document seed.  The JSON payload fallback below is
        # deliberately retained for legacy releases, but must not be on the
        # hot path: casting payload to text forces a sequential scan over the
        # whole release.  The generated document tsvector is release-scoped
        # and recovers short decisive phrases without that scan.
        indexed_ids: list[str] = []
        if phrase_disjunction:
            await self.session.execute(text("SET LOCAL statement_timeout = '1800ms'"))
            indexed = await self.session.execute(
                text(
                    """
                    SELECT d.id AS document_id,
                           ts_rank_cd(d.document_search_vector,
                                      to_tsquery('simple', :phrase_disjunction)) AS score
                    FROM documents d
                    WHERE d.dataset_id = :dataset_id
                      AND NOT d.is_external
                      AND COALESCE((d.payload -> 'metadata' ->> 'answer_ready')::boolean, FALSE) IS TRUE
                      AND (:include_local IS TRUE OR NOT (
                           COALESCE(d.payload -> 'metadata' ->> 'pham_vi', d.payload -> 'metadata' ->> 'pham_vi', '') ~* '(địa phương|tỉnh|thành phố|huyện)'
                           OR d.title ~* 'NQ-HĐND'
                      ))
                      AND d.document_search_vector @@ to_tsquery('simple', :phrase_disjunction)
                    ORDER BY score DESC, d.id
                    LIMIT :limit
                    """
                ),
                {"dataset_id": dataset_id, "phrase_disjunction": phrase_disjunction,
                 "include_local": include_local, "limit": limit},
            )
            indexed_ids = [str(row.document_id) for row in indexed]
            # Add a small head for the strongest contiguous phrase before the
            # broad OR-tsquery ranking.  This keeps a concise exclusion
            # provision discoverable even when many newer documents dominate
            # the generic lexical score.
            exact_phrase = next(iter(extract_query_phrases(query, limit=1)), "")
            if exact_phrase:
                phrase_head = await self.session.execute(
                    text(
                        """
                        SELECT d.id AS document_id
                        FROM documents d
                        WHERE d.dataset_id = :dataset_id
                          AND NOT d.is_external
                          AND COALESCE((d.payload -> 'metadata' ->> 'answer_ready')::boolean, FALSE) IS TRUE
                          AND (:include_local IS TRUE OR NOT (
                               COALESCE(d.payload -> 'metadata' ->> 'pham_vi', '') ~* '(địa phương|tỉnh|thành phố|huyện)'
                               OR d.title ~* 'NQ-HĐND'
                          ))
                          AND d.document_search_vector @@ websearch_to_tsquery('simple', :phrase)
                        ORDER BY ts_rank_cd(d.document_search_vector, websearch_to_tsquery('simple', :phrase)) DESC, d.id
                        LIMIT 16
                        """
                    ),
                    {"dataset_id": dataset_id, "phrase": exact_phrase, "include_local": include_local},
                )
                indexed_ids = list(dict.fromkeys([str(row.document_id) for row in phrase_head] + indexed_ids))
        # Fast path for exact user phrases.  This covers short provisions
        # (e.g. "dịch vụ thẩm mỹ") that may be absent from a document's
        # generated tsvector because of accent/tokenisation differences.
        # It is intentionally bounded and query-derived; no statute or
        # document identifier is embedded in the retrieval code.
        exact = " ".join(query.split())
        if len(exact) >= 3:
            await self.session.execute(text("SET LOCAL statement_timeout = '1200ms'"))
            exact_result = await self.session.execute(
                text(
                    """
                    SELECT d.id AS document_id
                    FROM documents d
                    WHERE d.dataset_id = :dataset_id
                      AND NOT d.is_external
                      AND COALESCE((d.payload -> 'metadata' ->> 'answer_ready')::boolean, FALSE) IS TRUE
                      AND (:include_local IS TRUE OR NOT (
                           COALESCE(d.payload -> 'metadata' ->> 'pham_vi', d.payload ->> 'pham_vi', '') ~* '(địa phương|tỉnh|thành phố|huyện)'
                           OR d.title ~* 'NQ-HĐND'
                      ))
                      AND (d.title ILIKE :contains OR d.payload::text ILIKE :contains)
                    ORDER BY ts_rank_cd(d.document_search_vector,
                                        websearch_to_tsquery('simple', :query)) DESC,
                             d.id
                    LIMIT :limit
                    """
                ),
                {"dataset_id": dataset_id, "query": query, "contains": f"%{exact}%", "include_local": include_local, "limit": limit},
            )
            exact_ids = [str(row.document_id) for row in exact_result]
        # If the complete question is not present verbatim, retry one
        # discriminative query-derived n-gram.  Prefer the longest two-word
        # phrase so short legal concepts survive surrounding interrogatives.
        phrase_candidates = [p for p in lexical_phrases(query, limit=48) if len(p.split()) == 2]
        if phrase_candidates:
            phrase_result = await self.session.execute(
                text(
                    """
                    SELECT d.id AS document_id
                    FROM documents d
                    WHERE d.dataset_id = :dataset_id
                      AND NOT d.is_external
                      AND COALESCE((d.payload -> 'metadata' ->> 'answer_ready')::boolean, FALSE) IS TRUE
                      AND (:include_local IS TRUE OR NOT (
                           COALESCE(d.payload -> 'metadata' ->> 'pham_vi', d.payload -> 'metadata' ->> 'pham_vi', '') ~* '(địa phương|tỉnh|thành phố|huyện)'
                           OR d.title ~* 'NQ-HĐND'
                      ))
                      AND EXISTS (
                          SELECT 1 FROM unnest(CAST(:phrases AS text[])) AS phrase
                          WHERE d.payload::text ILIKE '%' || phrase || '%'
                             OR d.document_search_vector @@ websearch_to_tsquery('simple', phrase)
                      )
                    ORDER BY (EXISTS (
                               SELECT 1 FROM unnest(CAST(:phrases AS text[])) AS exact_phrase
                               WHERE d.payload::text ILIKE '%' || exact_phrase || '%'
                             )) DESC,
                             (
                        SELECT max(ts_rank_cd(d.document_search_vector,
                                              websearch_to_tsquery('simple', phrase)))
                        FROM unnest(CAST(:phrases AS text[])) AS phrase
                    ) DESC,
                    d.id
                    LIMIT :limit
                    """
                ),
                {"dataset_id": dataset_id, "phrases": phrase_candidates[:24], "include_local": include_local, "limit": limit},
            )
            phrase_ids = [str(row.document_id) for row in phrase_result]
            if phrase_ids:
                return list(dict.fromkeys([*indexed_ids, *phrase_ids, *exact_ids]))[:limit]
            if exact_ids:
                return list(dict.fromkeys([*indexed_ids, *exact_ids]))[:limit]
        await self.session.execute(text("SET LOCAL statement_timeout = '2500ms'"))
        result = await self.session.execute(
            text(
                """
                WITH request AS (
                    SELECT to_tsquery('simple', :disjunction) AS term_query,
                           CASE WHEN :phrase_disjunction = '' THEN NULL::tsquery
                                ELSE to_tsquery('simple', :phrase_disjunction) END AS phrase_query
                ), ranked AS (
                    SELECT d.id AS document_id,
                           (request.phrase_query IS NOT NULL
                            AND d.document_search_vector @@ request.phrase_query) AS has_phrase,
                           GREATEST(
                               ts_rank_cd(d.document_search_vector, request.term_query),
                               CASE WHEN request.phrase_query IS NULL THEN 0.0
                                    ELSE ts_rank_cd(d.document_search_vector, request.phrase_query) * 4.0 END
                           ) AS score,
                           CASE
                               WHEN COALESCE(d.payload -> 'metadata' ->> 'loai_van_ban', '') ILIKE '%luật%'
                                    OR d.title ILIKE 'luật %' THEN 4
                               WHEN COALESCE(d.payload -> 'metadata' ->> 'loai_van_ban', '') ILIKE '%nghị định%'
                                    OR d.title ILIKE 'nghị định %' THEN 3
                               WHEN d.title ILIKE 'văn bản hợp nhất%' THEN 2
                               WHEN d.title ILIKE 'thông tư%' THEN 1
                               ELSE 0
                           END AS authority_rank,
                           CASE WHEN COALESCE(d.payload -> 'metadata' ->> 'legal_status_verified', 'false')::boolean
                                     AND COALESCE(d.payload -> 'metadata' ->> 'tinh_trang_hieu_luc', '')
                                         ILIKE 'còn hiệu lực%'
                                THEN 1 ELSE 0 END AS current_verified_rank
                    FROM documents d CROSS JOIN request
                    WHERE d.dataset_id = :dataset_id
                      AND NOT d.is_external
                      AND COALESCE((d.payload -> 'metadata' ->> 'answer_ready')::boolean, FALSE) IS TRUE
                      AND (:include_local IS TRUE OR NOT (
                           COALESCE(d.payload -> 'metadata' ->> 'pham_vi', d.payload ->> 'pham_vi', '') ~* '(địa phương|tỉnh|thành phố|huyện)'
                           OR d.title ~* 'NQ-HĐND'
                      ))
                      AND (
                          d.document_search_vector @@ request.term_query
                          OR (request.phrase_query IS NOT NULL
                              AND d.document_search_vector @@ request.phrase_query)
                      )
                )
                SELECT ranked.document_id FROM ranked
                ORDER BY has_phrase DESC,
                         (current_verified_rank = 1 OR authority_rank = 4) DESC,
                         authority_rank DESC,
                         score DESC,
                         current_verified_rank DESC,
                         ranked.document_id
                LIMIT :limit
                """
            ),
            {
                "dataset_id": dataset_id,
                "phrase_disjunction": phrase_disjunction,
                "disjunction": disjunction,
                "include_local": include_local,
                "limit": limit,
            },
        )
        # ``phrase_anchors`` and ``chunk_phrase_anchors`` already participate
        # in the query above and are the first ORDER BY key.  The previous
        # implementation repeated a second corpus-wide phrase scan after this
        # result, adding another managed-Postgres round trip for the same IDs.
        return list(dict.fromkeys([str(row.document_id) for row in result] + indexed_ids))[:limit]

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

    async def expand_internal_references(
        self,
        reference_targets: Sequence[tuple[str, str]],
        *,
        dataset_id: str,
        limit: int = 8,
    ) -> list[RetrievalResult]:
        """Find operative clauses in a selected document sharing a legal reference.

        This is a bounded canonical join, used for questions that need an
        amount, duration or condition.  For example, an implementation clause
        may identify students by ``điểm b khoản 4 Điều 12`` while a separate
        clause states the support percentage for that same reference.
        """
        targets = list(
            dict.fromkeys(
                (str(document_id), " ".join(reference.split()))
                for document_id, reference in reference_targets
                if document_id and reference.strip()
            )
        )
        if not targets or limit <= 0:
            return []
        result = await self.session.execute(
            text(
                """
                WITH matched AS (
                    SELECT c.chunk_id, c.document_id, c.text, c.section_title, c.unit_id,
                           c.source_start, c.source_end, c.text_sha256,
                           c.embedding_input_sha256, d.title,
                           min(ref.ordinality) AS reference_rank
                    FROM chunks c
                    JOIN documents d ON d.dataset_id = c.dataset_id AND d.id = c.document_id
                    JOIN unnest(CAST(:reference_document_ids AS text[]), CAST(:references AS text[]))
                         WITH ORDINALITY AS ref(document_id, reference, ordinality)
                      ON c.document_id = ref.document_id
                     AND (
                         regexp_replace(lower(COALESCE(c.section_title, '') || ' ' || c.text), '\\s+', ' ', 'g')
                         LIKE '%' || lower(ref.reference) || '%'
                      -- A current rule can cite several beneficiaries in a
                      -- compact form (for example “điểm b, c, đ, e và h
                      -- khoản 4 Điều 12”).  The article/paragraph tail is
                      -- still an exact legal boundary, while this branch
                      -- recovers the grouped-reference spelling.
                      OR regexp_replace(lower(COALESCE(c.section_title, '') || ' ' || c.text), '\\s+', ' ', 'g')
                         LIKE '%' || regexp_replace(lower(ref.reference), '^điểm [a-zđ] ', '') || '%'
                     )
                    WHERE c.dataset_id = :dataset_id
                      AND c.lexical_eligible IS TRUE
                      AND NOT d.is_external
                      AND COALESCE((d.payload -> 'metadata' ->> 'answer_ready')::boolean, FALSE) IS TRUE
                    GROUP BY c.chunk_id, c.document_id, c.text, c.section_title, c.unit_id,
                             c.source_start, c.source_end, c.text_sha256,
                             c.embedding_input_sha256, d.title
                )
                SELECT * FROM matched
                ORDER BY reference_rank, document_id, source_start NULLS LAST, chunk_id
                LIMIT :limit
                """
            ),
            {
                "dataset_id": dataset_id,
                "reference_document_ids": [item[0] for item in targets],
                "references": [item[1] for item in targets],
                "limit": limit,
            },
        )
        return [
            RetrievalResult(
                chunk_id=str(row.chunk_id), document_id=str(row.document_id), dataset_id=dataset_id,
                content=str(row.text or ""), source=str(row.document_id), title=str(row.title or ""),
                section_title=str(row.section_title or ""), unit_id=str(row.unit_id or ""),
                source_start=int(row.source_start) if row.source_start is not None else None,
                source_end=int(row.source_end) if row.source_end is not None else None,
                text_sha256=str(row.text_sha256 or ""), input_sha256=str(row.embedding_input_sha256 or ""),
                channels=["legal_reference"], score=1.0,
            )
            for row in result
        ]

    async def search_document_operatives(
        self,
        document_ids: Sequence[str],
        *,
        dataset_id: str,
        terms: Sequence[str],
        limit: int = 12,
        minimum_matches: int | None = None,
    ) -> list[RetrievalResult]:
        """Retrieve operative passages from an already selected legal document.

        Passage ANN can find the beneficiary clause while missing the separate
        percentage, duration or payment clause in the same decree.  This is a
        document-bounded lexical expansion, never a corpus-wide fallback.
        """
        ids = list(dict.fromkeys(str(value) for value in document_ids if value))
        needles = list(dict.fromkeys(" ".join(value.split()) for value in terms if len(value.strip()) >= 3))
        if not ids or not needles or limit <= 0:
            return []
        required_matches = minimum_matches if minimum_matches is not None else (2 if len(needles) > 1 else 1)
        required_matches = max(1, min(required_matches, len(needles)))
        phrase_candidates: list[tuple[int, str, list[str]]] = []
        query_tokens: list[str] = []
        for index, phrase in enumerate(needles[:64]):
            tokens = [token.casefold() for token in _LEXICAL_TOKEN.findall(phrase)]
            if len(tokens) >= 2:
                phrase_candidates.append((index, phrase, tokens))
                # Vietnamese legal sources commonly zero-pad one-digit
                # quantities ("05 năm") while users write "5 năm".  Add the
                # formatting-equivalent phrase; this is numeric
                # normalization, not a topic or authority mapping.
                if any(token.isdigit() and len(token) == 1 for token in tokens):
                    phrase_candidates.append((index, phrase, [token.zfill(2) if token.isdigit() and len(token) == 1 else token for token in tokens]))
            query_tokens.extend(tokens)
        query_tokens = list(dict.fromkeys(query_tokens))[:24]
        # A giant OR of every sliding window defeats the GIN index. Keep a
        # small query-derived portfolio: numeric qualifiers, longer concepts,
        # and a bounded set of raw bigrams (which recover paraphrases such as
        # ``cấp cứu`` when the user also supplied ``nội trú``).
        numeric = [row for row in phrase_candidates if any(token.isdigit() for token in row[2])][:3]
        # Keep enough contiguous 3+ token phrases to retain a decisive noun
        # phrase that appears after interrogative wording (for example
        # ``dịch vụ thẩm mỹ``). The final SQL remains document-bounded.
        long_phrases = [row for row in phrase_candidates if len(row[2]) >= 3 and row not in numeric][:16]
        bigrams = [row for row in phrase_candidates if len(row[2]) == 2 and row not in numeric][:6]
        selected_phrase_rows = numeric + long_phrases + bigrams
        phrase_clauses = ["(" + " <-> ".join(row[2]) + ")" for row in selected_phrase_rows]
        phrase_query = " | ".join(dict.fromkeys(phrase_clauses))
        term_query = " | ".join(query_tokens)
        if not term_query:
            return []
        # Protect the shared managed-Postgres pool from a pathological
        # document-bounded scan. Server-side cancellation returns the socket
        # promptly, unlike cancelling an asyncpg coroutine after the backend
        # has already started a large expression-index walk.
        # The bounded operative scan may inspect several canonical units per
        # document.  Cancelling it at 6s left asyncpg connections in an
        # invalid state under load; keep the server-side limit below the
        # high-risk route budget so it can finish and return grounded clauses.
        await self.session.execute(text("SET LOCAL statement_timeout = '12000ms'"))
        result = await self.session.execute(
            text(
                """
                WITH request AS (
                    SELECT CASE WHEN :phrase_query = ''
                                THEN to_tsquery('simple', :term_query)
                                ELSE to_tsquery('simple', :phrase_query) END AS search_query
                ), candidates AS (
                    SELECT c.unit_id, c.document_id, c.text, c.section_title AS heading,
                           c.section_title AS label, c.source_start, c.source_end, c.text_sha256,
                           COALESCE(d.payload -> 'metadata' ->> 'so_ky_hieu', d.payload ->> 'so_ky_hieu', '') AS document_number,
                           TRUE AS has_phrase,
                           CASE WHEN EXISTS (
                               SELECT 1 FROM unnest(CAST(:needles AS text[])) AS needle
                               WHERE lower(COALESCE(c.text, '') || ' ' || COALESCE(c.section_title, ''))
                                     LIKE '%' || lower(needle) || '%'
                           ) THEN 1 ELSE 0 END AS phrase_signal,
                           CASE WHEN COALESCE(c.text, '') ~ '[0-9]+[[:space:]]*%' THEN 1 ELSE 0 END AS value_signal,
                           ts_rank_cd(c.search_vector, request.search_query) AS seed_score
                    FROM chunks c CROSS JOIN request
                    LEFT JOIN documents d
                      ON d.dataset_id = c.dataset_id AND d.id::text = c.document_id::text
                    WHERE c.dataset_id = :dataset_id
                      AND c.document_id = ANY(CAST(:document_ids AS text[]))
                      AND c.lexical_eligible IS TRUE
                      AND c.search_vector @@ request.search_query
                    UNION ALL
                    -- Some canonical legal units are split across chunks and
                    -- the decisive exclusion/exception wording is retained
                    -- only in the parsed unit table. Restrict this fallback
                    -- to the already selected documents and query-derived
                    -- phrases; never scan the corpus-wide unit store.
                    SELECT u.unit_id, u.document_id, u.text, u.heading,
                           u.label, u.source_start, u.source_end, u.text_sha256,
                           COALESCE(d.payload -> 'metadata' ->> 'so_ky_hieu', d.payload ->> 'so_ky_hieu', '') AS document_number,
                           TRUE AS has_phrase,
                           CASE WHEN EXISTS (
                               SELECT 1 FROM unnest(CAST(:needles AS text[])) AS needle
                               WHERE lower(COALESCE(u.text, '') || ' ' || COALESCE(u.heading, '') || ' ' || COALESCE(u.label, ''))
                                     LIKE '%' || lower(needle) || '%'
                           ) THEN 1 ELSE 0 END AS phrase_signal,
                           CASE WHEN COALESCE(u.text, '') ~ '[0-9]+[[:space:]]*%' THEN 1 ELSE 0 END AS value_signal,
                           1.0::double precision AS seed_score
                    FROM legal_units u
                    LEFT JOIN documents d
                      ON d.dataset_id = u.dataset_id AND d.id::text = u.document_id::text
                    WHERE u.dataset_id = :dataset_id
                      AND u.document_id = ANY(CAST(:document_ids AS text[]))
                      AND EXISTS (
                          SELECT 1
                          FROM unnest(CAST(:needles AS text[])) AS needle
                          WHERE lower(COALESCE(u.text, '') || ' ' ||
                                      COALESCE(u.heading, '') || ' ' ||
                                      COALESCE(u.label, '')) LIKE '%' || lower(needle) || '%'
                      )
                ), diverse AS (
                    SELECT candidates.*,
                           row_number() OVER (
                               PARTITION BY document_id
                               ORDER BY phrase_signal DESC,
                                        value_signal DESC,
                                        seed_score DESC,
                                        length(COALESCE(NULLIF(text, ''), heading, label, '')),
                                        unit_id
                           ) AS document_rank
                    FROM candidates
                )
                SELECT unit_id, document_id, text, heading, label,
                       source_start, source_end, text_sha256, document_number,
                       has_phrase, seed_score
                FROM diverse
                WHERE document_rank <= :per_document_limit
                ORDER BY has_phrase DESC, phrase_signal DESC, value_signal DESC, seed_score DESC,
                         length(COALESCE(NULLIF(text, ''), heading, label, '')),
                         unit_id
                LIMIT :candidate_limit
                """
            ),
            {
                "dataset_id": dataset_id,
                "document_ids": ids,
                "term_query": term_query,
                "phrase_query": phrase_query,
                "needles": needles,
                # Enforce genuine document diversity before the global limit:
                # a few verbose instruments must not crowd out the governing
                # statute. One row is enough for narrow budgets; keep a small
                # eight-row legal-unit portfolio for larger operative scans so
                # entitlement, exception and scope clauses can coexist even
                # when numeric headings outrank the decisive prose clause.
                "per_document_limit": 1 if limit < 32 else 8,
                "candidate_limit": min(1024, max(64, len(ids) * (1 if limit < 32 else 8))),
            },
        )
        document_order = {identifier: index for index, identifier in enumerate(ids)}
        ranked: list[tuple[float, float, int, int, int, float, int, int, RetrievalResult]] = []
        for row in result:
            content = str(row.text or "")
            if not content:
                content = str(row.heading or row.label or "")
            searchable = f"{row.label or ''} {row.heading or ''} {content}".casefold()
            matched_phrases = sum(needle.casefold() in searchable for needle in needles)
            source_tokens = set(_LEXICAL_TOKEN.findall(searchable))
            token_matches = sum(token in source_tokens for token in query_tokens)
            if max(matched_phrases, token_matches) < required_matches:
                continue
            token_count = max(1, len(_LEXICAL_TOKEN.findall(searchable)))
            match_density = token_matches / token_count
            phrase_density = matched_phrases / token_count
            structural_bonus = int(bool(re.match(r"^\s*(?:[0-9]+|[a-zđ])[.)]\s", content.casefold())))
            item = RetrievalResult(
                chunk_id=f"unit:{row.unit_id}",
                document_id=str(row.document_id),
                dataset_id=dataset_id,
                content=content,
                source=str(row.document_id),
                title="",
                section_title=str(row.heading or row.label or ""),
                unit_id=str(row.unit_id or ""),
                source_start=int(row.source_start) if row.source_start is not None else None,
                source_end=int(row.source_end) if row.source_end is not None else None,
                text_sha256=str(row.text_sha256 or ""),
                document_number=str(row.document_number or ""),
                channels=["document_operatives", "page_index"],
                score=float(matched_phrases + match_density) + float(row.seed_score or 0.0),
            )
            ranked.append(
                (
                    phrase_density,
                    match_density,
                    structural_bonus,
                    matched_phrases,
                    token_matches,
                    float(row.seed_score or 0.0),
                    -len(content),
                    -document_order.get(str(row.document_id), len(ids)),
                    item,
                )
            )
        ranked.sort(key=lambda entry: entry[:8], reverse=True)
        # Preserve intra-document depth for the strongest independently
        # recalled authorities.  A global top-k alone is dominated by many
        # tiny headings from one verbose instrument and can discard the
        # decisive ninth/tenth clause of the governing statute.  The caller
        # still performs authority/value reranking and enforces the public
        # evidence ceiling.
        selected: list[RetrievalResult] = []
        selected_ids: set[str] = set()
        # Runtime commonly asks for 48 candidates across four primary
        # authorities. Keep twelve clauses per authority; deriving this quota
        # from ``limit // 8`` retained only six and cut the verified emergency
        # exception at intra-document rank eight.
        per_document_quota = min(12, max(8, limit // 4))
        for document_id in ids[:8]:
            document_rows = [entry for entry in ranked if entry[8].document_id == document_id]
            for entry in document_rows[:per_document_quota]:
                item = entry[8]
                if item.chunk_id in selected_ids:
                    continue
                selected_ids.add(item.chunk_id)
                selected.append(item)
                if len(selected) >= limit:
                    return selected
        for entry in ranked:
            item = entry[8]
            if item.chunk_id in selected_ids:
                continue
            selected_ids.add(item.chunk_id)
            selected.append(item)
            if len(selected) >= limit:
                break
        return selected

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

    async def hydrate_chunks_by_ids(
        self,
        chunk_ids: list[str],
    ) -> list[RetrievalResult]:
        """Hydrate chunk text content from PostgreSQL by chunk IDs."""
        if not chunk_ids:
            return []
        result = await self.session.execute(
            text(
                """
                SELECT c.chunk_id, c.document_id, c.text, c.section_title,
                       d.title
                FROM chunks c
                JOIN documents d ON d.dataset_id = c.dataset_id AND d.id = c.document_id
                WHERE c.chunk_id = ANY(:chunk_ids)
                """
            ),
            {"chunk_ids": list(chunk_ids)},
        )
        return [
            RetrievalResult(
                chunk_id=str(row.chunk_id),
                document_id=str(row.document_id),
                content=str(row.text or ""),
                source=str(row.document_id),
                title=str(row.title or ""),
                section_title=str(row.section_title or ""),
                score=0.0,
                channels=["hydrated"],
            )
            for row in result
        ]
