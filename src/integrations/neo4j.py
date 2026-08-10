"""Neo4j persistence adapter for the knowledge graph."""

from __future__ import annotations

from collections.abc import Sequence

from src.config import get_settings
from src.models.graph import Relation as RelationDTO


class Neo4jGraphStore:
    def __init__(self) -> None:
        from neo4j import AsyncGraphDatabase

        settings = get_settings()
        if not settings.neo4j_uri or not settings.neo4j_password:
            raise RuntimeError("NEO4J_URI and NEO4J_PASSWORD are required")
        self.database = settings.neo4j_database
        self.driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_username, settings.neo4j_password)
        )

    async def expand(self, entity_names: Sequence[str], hops: int = 1, limit: int = 20) -> list[RelationDTO]:
        if not entity_names or hops < 1:
            return []
        hops = min(hops, 5)
        query = f"""MATCH (source)-[r*1..{hops}]->(target)
        WHERE source.name IN $names OR target.name IN $names
        UNWIND r AS rel
        RETURN startNode(rel).name AS source_name, type(rel) AS relation_type,
               endNode(rel).name AS target_name, coalesce(rel.description, '') AS description
        LIMIT $limit"""
        async with self.driver.session(database=self.database) as session:
            result = await session.run(query, names=list(entity_names), limit=limit)
            rows = await result.data()
        return [RelationDTO(source=row["source_name"], target=row["target_name"], relation_type=row["relation_type"], description=row["description"]) for row in rows]

    async def close(self) -> None:
        await self.driver.close()
