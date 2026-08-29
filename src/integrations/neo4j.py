"""Neo4j persistence adapter for the knowledge graph."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Sequence
from contextlib import asynccontextmanager
from typing import Any

from src.config import get_settings
from src.domain.facts import LegalFact
from src.models.graph import Relation

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _session_context(driver: Any, **kwargs: Any):
    """Normalize Neo4j session factories before entering context."""
    session = driver.session(**kwargs)
    if inspect.isawaitable(session):
        session = await session
    # ``driver.session()`` may return either an async context manager or an
    # awaitable resolving to one.  Yield the value returned by ``__aenter__``
    # rather than the wrapper itself; this keeps lightweight test doubles and
    # Neo4j's native AsyncSession behavior consistent.
    async with session as entered_session:
        yield entered_session


class Neo4jGraphStore:
    def __init__(self) -> None:
        from neo4j import AsyncGraphDatabase

        settings = get_settings()
        if not settings.neo4j_uri or not settings.neo4j_password:
            raise RuntimeError("NEO4J_URI and NEO4J_PASSWORD are required")
        self.database = settings.neo4j_database
        self.driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_username, settings.neo4j_password),
            # Keep Aura connection management deliberately small.  The graph
            # is an optional navigation projection, not a request-sized pool.
            max_connection_pool_size=10,
            connection_timeout=5.0,
            connection_acquisition_timeout=5.0,
            max_transaction_retry_time=5.0,
            keep_alive=True,
        )

    async def verify_connectivity(self) -> None:
        await self.driver.verify_connectivity()

    def session_context(self, **kwargs: Any):
        """Return normalized session context for callers that need a session."""
        return _session_context(self.driver, **kwargs)

    async def readiness(
        self,
        *,
        dataset_id: str,
        expected_nodes: int | None = None,
        expected_approved_edges: int | None = None,
    ) -> bool:
        """Check release-scoped node/edge counts, not connectivity alone."""
        async with _session_context(self.driver, database=self.database) as session:
            result = await session.run(
                """
                MATCH (n)
                WHERE n.dataset_id = $dataset_id
                WITH count(n) AS node_count
                OPTIONAL MATCH ()-[r]->()
                WHERE r.dataset_id = $dataset_id
                  AND r.serving_status = 'approved_evidence'
                RETURN node_count, count(r) AS relationship_count
                """,
                dataset_id=dataset_id,
            )
            row = await result.single()
        if not row:
            return False
        node_count = int(row["node_count"])
        relationship_count = int(row["relationship_count"])
        # A release projection can be safely additive: a reconciler may have
        # retained audit-only nodes/edges from the same immutable release.
        # Requiring exact equality made harmless graph drift turn `/ready`
        # red and blocked the whole API, even though the approved evidence
        # subgraph was queryable.  Lower bounds still catch a partial or empty
        # projection while allowing the graph to serve during reconciliation.
        if expected_nodes is not None and node_count < expected_nodes:
            logger.warning(
                "Neo4j release has fewer nodes than its contract (dataset=%s actual=%s expected_min=%s)",
                dataset_id,
                node_count,
                expected_nodes,
            )
            return False
        if expected_approved_edges is not None and relationship_count < expected_approved_edges:
            logger.warning(
                "Neo4j release has fewer approved edges than its contract "
                "(dataset=%s actual=%s expected_min=%s)",
                dataset_id,
                relationship_count,
                expected_approved_edges,
            )
            return False
        return node_count > 0 and relationship_count > 0

    async def expand(
        self,
        entity_names: Sequence[str],
        *,
        dataset_id: str,
        hops: int = 1,
        limit: int = 20,
    ) -> list[Relation]:
        if not entity_names or hops < 1:
            return []
        hops = min(hops, 5)
        query = f"""
        MATCH (source:Document)-[path*1..{hops}]->(target:Document)
        WHERE source.dataset_id = $dataset_id
          AND target.dataset_id = $dataset_id
          AND (source.id IN $ids OR target.id IN $ids)
          AND ALL(rel IN path WHERE type(rel) <> 'ALIAS_OF'
              AND rel.serving_status = 'approved_evidence')
        UNWIND path AS rel
        RETURN startNode(rel).id AS source_id,
               startNode(rel).name AS source_name,
               coalesce(rel.relationship_type, type(rel)) AS relation_type,
               endNode(rel).id AS target_id,
               endNode(rel).name AS target_name,
               coalesce(rel.relationship_type, '') AS description,
               coalesce(rel.relationship_id, '') AS relationship_id,
               coalesce(rel.adverse, false) AS adverse,
               CASE WHEN startNode(rel).id IN $ids THEN 'outbound' ELSE 'inbound' END AS direction
        LIMIT $limit
        """
        async with _session_context(self.driver, database=self.database) as session:
            result = await session.run(
                query,
                ids=list(entity_names),
                dataset_id=dataset_id,
                limit=limit,
            )
            rows = await result.data()
        return [
            Relation(
                source=str(row.get("source_name") or row.get("source_id") or ""),
                target=str(row.get("target_name") or row.get("target_id") or ""),
                source_id=str(row.get("source_id") or ""),
                target_id=str(row.get("target_id") or ""),
                relation_type=str(row.get("relation_type") or "RELATED"),
                description=str(row.get("description") or ""),
                relationship_id=str(row.get("relationship_id") or ""),
                adverse=bool(row.get("adverse")),
                direction=str(row.get("direction") or ""),
            )
            for row in rows
        ]

    async def upsert_legal_facts(self, facts: Sequence[LegalFact]) -> int:
        """Project reviewed-source facts into a release-scoped typed graph.

        PostgreSQL remains the canonical text store.  Neo4j receives only
        normalized fact metadata and provenance anchors; callers must still
        hydrate ``document_id/unit_id/source_*`` from PostgreSQL before citing.
        """
        if any(fact.review_status != "accepted" for fact in facts):
            raise ValueError("only accepted legal facts may be projected")
        if any(fact.source_start is None or fact.source_end is None for fact in facts):
            raise ValueError("typed fact projection requires a canonical source span")
        records = [fact.as_record() for fact in facts]
        if not records:
            return 0
        query = """
        UNWIND $facts AS fact
        MERGE (subject:FactSubject {key: fact.release_id + ':' + fact.subject})
        SET subject.dataset_id = fact.release_id,
            subject.name = fact.subject
        MERGE (value:FactValue {
            key: fact.release_id + ':' + fact.predicate + ':' + fact.normalized_value
        })
        SET value.dataset_id = fact.release_id,
            value.name = fact.normalized_value
        MERGE (subject)-[edge:LEGAL_FACT {fact_id: fact.fact_id}]->(value)
        SET edge.dataset_id = fact.release_id,
            edge.predicate = fact.predicate,
            edge.normalized_value = fact.normalized_value,
            edge.effective_from = fact.effective_from,
            edge.effective_to = fact.effective_to,
            edge.jurisdiction = fact.jurisdiction,
            edge.provision_id = fact.provision_id,
            edge.document_id = fact.document_id,
            edge.unit_id = fact.unit_id,
            edge.source_start = fact.source_start,
            edge.source_end = fact.source_end,
            edge.source_sha256 = fact.source_sha256,
            edge.review_status = fact.review_status
        RETURN count(edge) AS count
        """
        async with self.driver.session(database=self.database) as session:
            result = await session.run(query, facts=records)
            row = await result.single()
        return int(row["count"]) if row else 0

    async def expand_typed_facts(
        self,
        subjects: Sequence[str],
        *,
        dataset_id: str,
        limit: int = 20,
    ) -> list[Relation]:
        """Return only accepted, release-scoped fact edges for relational routes."""
        if not subjects or not dataset_id:
            return []
        query = """
        MATCH (subject:FactSubject)-[edge:LEGAL_FACT]->(value:FactValue)
        WHERE subject.dataset_id = $dataset_id
          AND edge.dataset_id = $dataset_id
          AND edge.review_status = 'accepted'
          AND subject.name IN $subjects
        RETURN subject.name AS subject,
               value.name AS value,
               edge.predicate AS predicate,
               edge.fact_id AS fact_id,
               edge.document_id AS document_id,
               edge.unit_id AS unit_id,
               edge.source_start AS source_start,
               edge.source_end AS source_end
        ORDER BY edge.fact_id
        LIMIT $limit
        """
        async with self.driver.session(database=self.database) as session:
            result = await session.run(
                query,
                dataset_id=dataset_id,
                subjects=list(dict.fromkeys(str(item) for item in subjects))[:limit],
                limit=max(1, min(int(limit), 100)),
            )
            rows = await result.data()
        return [
            Relation(
                source=str(row.get("subject") or ""),
                target=str(row.get("value") or ""),
                source_id=str(row.get("document_id") or ""),
                target_id=str(row.get("unit_id") or ""),
                relation_type=str(row.get("predicate") or "LEGAL_FACT"),
                description=(
                    f"{row.get('predicate') or 'LEGAL_FACT'}: "
                    f"{row.get('value') or ''}"
                ).strip(),
                relationship_id=str(row.get("fact_id") or ""),
                direction="outbound",
            )
            for row in rows
        ]

    async def bounded_typed_ppr(
        self,
        subjects: Sequence[str],
        *,
        dataset_id: str,
        depth: int = 2,
        fanout: int = 3,
    ) -> list[Relation]:
        """Perform a deterministic bounded-PPR-style typed fact walk.

        Neo4j deployments without the optional GDS plugin use this bounded
        path walk as the portable fallback: only accepted edges in one release
        are traversed, depth is capped at two, and fanout is capped at three.
        It is a navigation signal, never a citation without PostgreSQL
        hydration.
        """
        if not subjects or not dataset_id:
            return []
        bounded_depth = max(1, min(int(depth), 2))
        bounded_fanout = max(1, min(int(fanout), 3))
        query = f"""
        MATCH p=(subject:FactSubject)-[edges:LEGAL_FACT*1..{bounded_depth}]->(value:FactValue)
        WHERE subject.dataset_id = $dataset_id
          AND subject.name IN $subjects
          AND ALL(edge IN edges
              WHERE edge.dataset_id = $dataset_id
                AND edge.review_status = 'accepted')
        WITH subject, value, edges, length(p) AS hops
        RETURN subject.name AS subject,
               value.name AS value,
               last(edges).predicate AS predicate,
               last(edges).fact_id AS fact_id,
               last(edges).document_id AS document_id,
               last(edges).unit_id AS unit_id,
               hops
        ORDER BY hops ASC, fact_id
        LIMIT $limit
        """
        async with self.driver.session(database=self.database) as session:
            result = await session.run(
                query,
                dataset_id=dataset_id,
                subjects=list(dict.fromkeys(str(item) for item in subjects))[:bounded_fanout],
                limit=bounded_fanout * bounded_depth,
            )
            rows = await result.data()
        return [
            Relation(
                source=str(row.get("subject") or ""),
                target=str(row.get("value") or ""),
                source_id=str(row.get("document_id") or ""),
                target_id=str(row.get("unit_id") or ""),
                relation_type=str(row.get("predicate") or "LEGAL_FACT"),
                description=(
                    f"{row.get('predicate') or 'LEGAL_FACT'}: "
                    f"{row.get('value') or ''}"
                ).strip(),
                relationship_id=str(row.get("fact_id") or ""),
                direction="outbound",
            )
            for row in rows
        ]

    async def close(self) -> None:
        await self.driver.close()


__all__ = ["Neo4jGraphStore"]
