from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import DocumentChunk
from src.models.graph import Relation as RelationDTO
from src.models.graph import RetrievalResult


class GraphRepository:
    """Persistence boundary for Supabase vector and graph queries."""

    def __init__(self, session: AsyncSession):
        self.session = session

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
        query = text(
            """
            SELECT source.name AS source_name, r.relation_type,
                   target.name AS target_name, r.description
            FROM relations r
            JOIN entities source ON source.id = r.source_entity_id
            JOIN entities target ON target.id = r.target_entity_id
            WHERE source.name = ANY(:entity_names)
               OR target.name = ANY(:entity_names)
            LIMIT :limit
            """
        )
        rows = (await self.session.execute(query, {"entity_names": entity_names, "limit": limit})).mappings()
        return [
            RelationDTO(
                source=row["source_name"],
                target=row["target_name"],
                relation_type=row["relation_type"],
                description=row["description"] or "",
            )
            for row in rows
        ]
