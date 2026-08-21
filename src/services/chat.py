from __future__ import annotations

import asyncio
from collections.abc import Sequence
from functools import lru_cache

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import text

from src.agents.prompts import NO_EVIDENCE_RESPONSE, SYSTEM_PROMPT
from src.config import get_settings
from src.db.repositories import GraphRepository
from src.db.session import session_scope
from src.integrations.embeddings import EmbeddingModel, get_embedding_model
from src.integrations.langfuse import llm_invoke_config, trace_span
from src.integrations.neo4j import Neo4jGraphStore
from src.models.graph import RetrievalResult
from src.services.llm import get_llm
from qdrant_client import QdrantClient


class GraphRagUnavailableError(RuntimeError):
    """A required GraphRAG dependency is unavailable."""


class ChatProviderError(RuntimeError):
    """The configured chat provider failed to generate a response."""


class GraphRagRuntime:
    """Own request-time GraphRAG dependencies and their lifecycle."""

    def __init__(self) -> None:
        self._embeddings: EmbeddingModel | None = None
        self._graph_store: Neo4jGraphStore | None = None
        self._qdrant_client: QdrantClient | None = None

    def _get_embeddings(self) -> EmbeddingModel:
        if self._embeddings is None:
            self._embeddings = get_embedding_model()
        return self._embeddings

    def _get_graph_store(self) -> Neo4jGraphStore:
        if self._graph_store is None:
            self._graph_store = Neo4jGraphStore()
        return self._graph_store

    def _get_qdrant_client(self) -> QdrantClient:
        if self._qdrant_client is None:
            settings = get_settings()
            self._qdrant_client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
        return self._qdrant_client

    async def _expand_entities_from_qdrant(
        self,
        qdrant_client: QdrantClient,
        collection_name: str,
        document_ids: list[str],
        query_vector: list[float],
        settings,
    ) -> list:
        from src.models.graph import Relation

        relations: list[Relation] = []
        if not document_ids:
            return relations

        # For now, return empty relations as the Qdrant payload may not have graph data
        # This can be enhanced later if graph relations are stored in Qdrant
        return relations

    async def retrieve(self, query: str) -> tuple[list, list]:
        async with trace_span(
            "retrieve-context",
            as_type="retriever",
            input={"query": query},
        ) as span:
            evidence, relations = await self._retrieve(query)
            if span is not None:
                span.update(
                    output={
                        "evidence_count": len(evidence),
                        "relation_count": len(relations),
                        "chunk_ids": [item.chunk_id for item in evidence[:20]],
                    }
                )
            return evidence, relations

    async def _retrieve(self, query: str) -> tuple[list, list]:
        settings = get_settings()
        try:
            embeddings = self._get_embeddings()
            graph_store = self._get_graph_store()
            async with trace_span("neo4j-connectivity") as span:
                await asyncio.wait_for(graph_store.verify_connectivity(), timeout=5)
                if span is not None:
                    span.update(output={"ok": True})
            async with trace_span(
                "embedding-query",
                as_type="embedding",
                input={"query_length": len(query)},
                metadata={"model": settings.embedding_model},
            ) as span:
                vector = await embeddings.embed_query(query)
                if span is not None:
                    span.update(
                        output={
                            "query_length": len(query),
                            "embedding_dimensions": len(vector),
                        }
                    )

            # Search vectors from Qdrant
            qdrant_client = self._get_qdrant_client()
            collection_name = settings.qdrant_collection
            async with trace_span(
                "qdrant-search",
                as_type="retriever",
                metadata={
                    "collection": collection_name,
                    "similarity_threshold": settings.semantic_similarity_threshold,
                },
            ) as span:
                from qdrant_client.models import Filter, FieldCondition, MatchValue

                search_result = qdrant_client.query_points(
                    collection_name=collection_name,
                    query=vector,
                    limit=settings.retrieval_top_k,
                    score_threshold=settings.semantic_similarity_threshold,
                )
                vector_results = []
                for point in search_result.points:
                    payload = point.payload or {}
                    vector_results.append(RetrievalResult(
                        chunk_id=payload.get("passage_id", str(point.id)),
                        document_id=payload.get("document_id", ""),
                        content=payload.get("text", ""),
                        source=payload.get("document_id", ""),
                        title=payload.get("title", ""),
                        section_title=payload.get("section_title", ""),
                        score=max(0.0, float(point.score)),
                        channels=["semantic"],
                    ))
                if span is not None:
                    span.update(
                        output={
                            "result_count": len(vector_results),
                            "top_k": settings.retrieval_top_k,
                        }
                    )
                document_ids = list(dict.fromkeys(item.document_id for item in vector_results))

                # Hydrate text content from PostgreSQL
                async with session_scope() as session:
                    repository = GraphRepository(session, graph_store)
                    chunk_ids = [item.chunk_id for item in vector_results]
                    hydrated = await repository.hydrate_chunks_by_ids(chunk_ids)
                    # Merge hydrated data into vector_results
                    hydrated_map = {h.chunk_id: h for h in hydrated}
                    for i, item in enumerate(vector_results):
                        if item.chunk_id in hydrated_map:
                            h = hydrated_map[item.chunk_id]
                            vector_results[i] = RetrievalResult(
                                chunk_id=item.chunk_id,
                                document_id=item.document_id,
                                content=h.content,
                                source=item.source,
                                title=h.title,
                                section_title=h.section_title,
                                score=item.score,
                                channels=item.channels,
                            )

                async with trace_span(
                    "neo4j-expand",
                    as_type="retriever",
                    metadata={"collection": collection_name},
                ) as span:
                    graph_results = await self._expand_entities_from_qdrant(
                        qdrant_client, collection_name, document_ids, vector, settings
                    )
                    if span is not None:
                        span.update(
                            output={
                                "relation_count": len(graph_results),
                                "document_count": len(document_ids),
                            }
                        )
            return _limit_evidence(
                vector_results,
                settings.max_llm_evidence,
            ), graph_results
        except GraphRagUnavailableError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise GraphRagUnavailableError("GraphRAG dependencies are unavailable") from exc
        except Exception as exc:
            raise GraphRagUnavailableError("GraphRAG retrieval failed") from exc

    async def generate(self, query: str, context: str) -> str:
        settings = get_settings()
        try:
            llm = get_llm()
            result = await asyncio.wait_for(
                llm.ainvoke(
                    [
                        SystemMessage(content=SYSTEM_PROMPT),
                        HumanMessage(
                            content=(
                                f"Câu hỏi người dùng:\n{query}\n\n"
                                f"Evidence và graph relations được phép sử dụng:\n{context}"
                            )
                        ),
                    ],
                    config=llm_invoke_config() or None,
                ),
                timeout=settings.llm_timeout_seconds,
            )
        except TimeoutError:
            return NO_EVIDENCE_RESPONSE
        except Exception as exc:
            raise ChatProviderError("Chat provider failed") from exc
        content = result.content
        if isinstance(content, str):
            return content
        if isinstance(content, Sequence):
            return "".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        return ""

    async def readiness(self) -> dict[str, bool]:
        settings = get_settings()
        checks = {
            "llm": settings.llm_configured,
            "embedding": bool(settings.openai_api_key and settings.embedding_model),
            "database": False,
            "neo4j": False,
        }
        if settings.app_env == "test":
            return {**checks, "database": True, "neo4j": True}
        try:
            async with session_scope() as session:
                await session.execute(text("SELECT 1"))
            checks["database"] = True
        except Exception:
            pass
        try:
            await asyncio.wait_for(self._get_graph_store().verify_connectivity(), timeout=5)
            checks["neo4j"] = True
        except Exception:
            pass
        return checks

    async def close(self) -> None:
        if self._graph_store is not None:
            await self._graph_store.close()
            self._graph_store = None
        if self._qdrant_client is not None:
            self._qdrant_client = None
        self._embeddings = None


def _merge_evidence(vector_results: list, graph_results: list) -> list:
    merged: dict[str, object] = {}
    for item in [*vector_results, *graph_results]:
        current = merged.get(item.chunk_id)
        if current is None:
            merged[item.chunk_id] = item
            continue
        current.channels = sorted(set([*current.channels, *item.channels]))
        current.score = max(current.score, item.score)
    return list(merged.values())


def _limit_evidence(evidence: list, limit: int) -> list:
    """Keep highest-ranked unique evidence within configured citation budget."""
    if limit <= 0:
        return []
    ranked = sorted(
        evidence,
        key=lambda item: (-float(getattr(item, "score", 0.0)), str(item.chunk_id)),
    )
    return ranked[:limit]


@lru_cache
def get_runtime() -> GraphRagRuntime:
    return GraphRagRuntime()


__all__ = [
    "ChatProviderError",
    "GraphRagRuntime",
    "GraphRagUnavailableError",
    "NO_EVIDENCE_RESPONSE",
    "SYSTEM_PROMPT",
    "get_runtime",
]
