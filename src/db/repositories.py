from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import DocumentChunk
from src.integrations.neo4j import Neo4jGraphStore
from src.models.graph import RetrievalResult


class GraphRepository:
    """Persistence boundary: vectors in Supabase, graph traversal in Neo4j."""

    def __init__(self, session: AsyncSession, graph_store: Neo4jGraphStore | None = None):
        self.session = session
        self.graph_store = graph_store

    async def search_vectors(
        self, embedding: Sequence[float], limit: int = 5
    ) -> list[RetrievalResult]:
        if not embedding:
            return []
        distance = DocumentChunk.embedding.cosine_distance(embedding).label("distance")
        query = (
            select(DocumentChunk, distance)
            .where(DocumentChunk.embedding.is_not(None))
            .order_by(distance)
            .limit(limit)
        )
        rows = (await self.session.execute(query)).all()
        return [
            RetrievalResult(
                chunk_id=str(chunk.id),
                content=chunk.content,
                source=chunk.source_uri,
                score=max(0.0, 1.0 - float(distance_value)),
            )
            for chunk, distance_value in rows
        ]

    async def expand_entities(
        self, entity_names: list[str], hops: int = 1, limit: int = 20
    ) -> list[RelationDTO]:
        if not entity_names or hops < 1:
            return []
        if self.graph_store is None:
            return []
        return await self.graph_store.expand(entity_names, hops=hops, limit=limit)
