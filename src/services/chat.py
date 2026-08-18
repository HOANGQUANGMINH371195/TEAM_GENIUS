from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Sequence
from dataclasses import dataclass
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
from src.integrations.qdrant import QdrantVectorStore, VectorHit
from src.models.graph import DocumentCandidate, RetrievalResult
from src.services.llm import get_llm
from src.services.retrieval import (
    extract_document_numbers,
    extract_legal_labels,
    is_metadata_question,
    policy_response,
    retrieval_intent,
    weighted_rrf,
)


class GraphRagUnavailableError(RuntimeError):
    """A required GraphRAG dependency is unavailable."""


class ChatProviderError(RuntimeError):
    """The configured chat provider failed to generate a response."""


@dataclass(frozen=True)
class RetrievalBundle:
    evidence: list[RetrievalResult]
    relations: list
    direct_response: str = ""


class GraphRagRuntime:
    """Own request-time GraphRAG dependencies and their lifecycle."""

    def __init__(self) -> None:
        self._embeddings: EmbeddingModel | None = None
        self._graph_store: Neo4jGraphStore | None = None
        self._vector_store: QdrantVectorStore | None = None
        self._active_release: tuple[str, int, float] | None = None
        self._embedding_cache: dict[str, tuple[list[float], float]] = {}

    def _get_embeddings(self) -> EmbeddingModel:
        if self._embeddings is None:
            self._embeddings = get_embedding_model()
        return self._embeddings

    def _get_graph_store(self) -> Neo4jGraphStore:
        if self._graph_store is None:
            self._graph_store = Neo4jGraphStore()
        return self._graph_store

    def _get_vector_store(self) -> QdrantVectorStore:
        if self._vector_store is None:
            self._vector_store = QdrantVectorStore()
        return self._vector_store

    async def retrieve(self, query: str) -> tuple[list, list]:
        bundle = await self.retrieve_bundle(query)
        return bundle.evidence, bundle.relations

    async def retrieve_bundle(self, query: str) -> RetrievalBundle:
        safe_response = policy_response(query)
        if safe_response:
            return RetrievalBundle(evidence=[], relations=[], direct_response=safe_response)
        async with trace_span(
            "retrieve-context",
            as_type="retriever",
            input={"query": query},
        ) as span:
            bundle = await self._retrieve(query)
            if span is not None:
                span.update(
                    output={
                        "evidence_count": len(bundle.evidence),
                        "relation_count": len(bundle.relations),
                        "chunk_ids": [item.chunk_id for item in bundle.evidence],
                        "direct": bool(bundle.direct_response),
                    }
                )
            return bundle

    async def _active_dataset(self, repository: GraphRepository) -> tuple[str, int]:
        now = time.monotonic()
        if self._active_release and now - self._active_release[2] < 30:
            return self._active_release[0], self._active_release[1]
        release = await repository.current_dataset_release()
        if release is None:
            raise GraphRagUnavailableError("No active dataset is available")
        self._active_release = (release[0], release[1], now)
        return release

    async def _embed_query(self, query: str) -> Sequence[float]:
        """Cache normalized query vectors briefly; embeddings are immutable per model."""
        key = " ".join(query.casefold().split())
        now = time.monotonic()
        cached = self._embedding_cache.get(key)
        if cached and now - cached[1] < 300:
            return cached[0]
        vector = [float(value) for value in await self._get_embeddings().embed_query(query)]
        if len(self._embedding_cache) >= 256:
            oldest = min(self._embedding_cache, key=lambda item: self._embedding_cache[item][1])
            self._embedding_cache.pop(oldest, None)
        self._embedding_cache[key] = (vector, now)
        return vector

    async def _retrieve(self, query: str) -> RetrievalBundle:
        settings = get_settings()
        try:
            async with session_scope() as session:
                repository = GraphRepository(session)
                async with trace_span("get-current-dataset") as span:
                    dataset_id, expected_points = await self._active_dataset(repository)
                    if span is not None:
                        span.update(output={"dataset_id": dataset_id, "expected_qdrant_points": expected_points})
                document_numbers = extract_document_numbers(query)
                exact_candidates: list[DocumentCandidate] = []
                for number in document_numbers:
                    exact_candidates.extend(await repository.find_documents(number, dataset_id=dataset_id, limit=3))
                exact_candidates = list({candidate.document_id: candidate for candidate in exact_candidates}.values())
                if is_metadata_question(query) and len(exact_candidates) == 1 and exact_candidates[0].answer_ready:
                    return RetrievalBundle([], [], _format_metadata_answer(query, exact_candidates[0]))
                exact_document_ids = [candidate.document_id for candidate in exact_candidates if candidate.answer_ready]
                page_results = _verified_evidence(await repository.resolve_legal_units(
                    extract_legal_labels(query), dataset_id=dataset_id, document_ids=exact_document_ids
                ))
                if page_results:
                    lexical_results = await repository.search_lexical(
                        query, dataset_id=dataset_id, document_ids=exact_document_ids,
                        limit=max(8, settings.retrieval_top_k),
                    )
                    return RetrievalBundle(
                        evidence=_verified_evidence(weighted_rrf(
                            {"page_index": page_results, "lexical": lexical_results},
                            limit=settings.max_llm_evidence,
                        )),
                        relations=[],
                    )

                # PostgreSQL lexical search and the remote embedding request do not depend on each other.
                lexical_task = asyncio.create_task(
                    repository.search_lexical(query, dataset_id=dataset_id, limit=max(20, settings.retrieval_top_k * 3))
                )
                async with trace_span("embedding-query", as_type="embedding", input={"query_length": len(query)}, metadata={"model": settings.embedding_model}) as span:
                    vector = await self._embed_query(query)
                    if span is not None:
                        span.update(output={"embedding_dimensions": len(vector)})
                lexical_results = await lexical_task
                if len(vector) != settings.embedding_dimensions:
                    raise GraphRagUnavailableError("Query embedding has unexpected dimensions")
                async with trace_span("qdrant-search", as_type="retriever", metadata={"dataset_id": dataset_id}) as span:
                    vector_hits = await self._get_vector_store().search(
                        vector, dataset_id=dataset_id, limit=max(20, settings.retrieval_top_k * 3),
                        score_threshold=settings.semantic_similarity_threshold,
                    )
                    if span is not None:
                        span.update(output={"result_count": len(vector_hits)})
                semantic_results = await _hydrate_vector_hits(repository, vector_hits, dataset_id)
                channels: dict[str, Sequence[RetrievalResult]] = {
                    "lexical": lexical_results,
                    "semantic": semantic_results,
                }
                intent = retrieval_intent(query)
                graph_results: list = []
                seed_ids = list(dict.fromkeys(item.document_id for item in weighted_rrf(channels, limit=6)))
                if intent in {"temporal", "relational"} and seed_ids:
                    graph_store = self._get_graph_store()
                    graph_repository = GraphRepository(session, graph_store)
                    async with trace_span("neo4j-expand", as_type="retriever", metadata={"dataset_id": dataset_id}) as span:
                        graph_results = await graph_repository.expand_entities(
                            seed_ids, dataset_id=dataset_id,
                            hops=min(settings.graph_hops, 2 if intent == "temporal" else 1),
                            limit=settings.graph_neighbor_limit,
                        )
                        if span is not None:
                            span.update(output={"relation_count": len(graph_results)})
                    related_ids = list(dict.fromkeys(
                        identifier for relation in graph_results
                        for identifier in (relation.source_id, relation.target_id)
                        if identifier and identifier not in seed_ids
                    ))[: settings.graph_evidence_limit]
                    if related_ids:
                        graph_lexical = await repository.search_lexical(
                            query, dataset_id=dataset_id, document_ids=related_ids,
                            limit=settings.graph_evidence_limit,
                        )
                        graph_semantic = await _hydrate_vector_hits(
                            repository,
                            await self._get_vector_store().search(
                                vector, dataset_id=dataset_id, document_ids=related_ids,
                                limit=settings.graph_evidence_limit, score_threshold=settings.semantic_similarity_threshold,
                            ),
                            dataset_id,
                        )
                        channels["legal_graph"] = _merge_evidence(graph_lexical, graph_semantic)
                return RetrievalBundle(
                    evidence=_verified_evidence(weighted_rrf(channels, limit=settings.max_llm_evidence)),
                    relations=graph_results,
                )
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
            "qdrant": False,
            "neo4j": False,
        }
        if settings.app_env == "test":
            return {**checks, "database": True, "qdrant": True, "neo4j": True}
        try:
            async with session_scope() as session:
                await session.execute(text("SELECT 1"))
                release = await GraphRepository(session).current_dataset_release()
            checks["database"] = True
            if release is not None:
                checks["qdrant"] = await asyncio.wait_for(
                    self._get_vector_store().readiness(dataset_id=release[0], expected_points=release[1]), timeout=10
                )
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
        if self._vector_store is not None:
            await self._vector_store.close()
            self._vector_store = None
        self._embeddings = None
        self._active_release = None
        self._embedding_cache.clear()


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


async def _hydrate_vector_hits(repository: GraphRepository, hits: Sequence[VectorHit], dataset_id: str) -> list[RetrievalResult]:
    hydrated = await repository.hydrate_chunks([item.chunk_id for item in hits], dataset_id=dataset_id)
    by_id = {item.chunk_id: item for item in hits}
    verified: list[RetrievalResult] = []
    for item in hydrated:
        hit = by_id.get(item.chunk_id)
        if hit is None or (item.input_sha256 and hit.input_sha256 != item.input_sha256):
            continue
        item.score = hit.score
        item.rank_details = {"semantic_raw_score": hit.score}
        verified.append(item)
    return verified


def _format_metadata_answer(query: str, document: DocumentCandidate) -> str:
    lowered = query.casefold()
    label = document.so_ky_hieu or document.document_id
    values: list[str] = [f"Văn bản {label}: {document.title}."]
    if any(token in lowered for token in ("hiệu lực", "tình trạng")):
        values.append(f"Tình trạng: {document.legal_status or 'chưa xác định'}.")
        if document.ngay_co_hieu_luc:
            values.append(f"Có hiệu lực từ: {document.ngay_co_hieu_luc}.")
    if "ban hành" in lowered and document.ngay_ban_hanh:
        values.append(f"Ngày ban hành: {document.ngay_ban_hanh}.")
    if any(token in lowered for token in ("danh mục", "category", "thuộc")) and document.categories:
        values.append("Nhóm: " + ", ".join(document.categories) + ".")
    return " ".join(values)


def _verified_evidence(evidence: Sequence[RetrievalResult]) -> list[RetrievalResult]:
    """Reject stale or mixed-release text before it reaches an LLM/citation."""
    return [
        item for item in evidence
        if ("page_index" in item.channels and item.source_start is not None and item.source_end is not None)
        or not item.text_sha256
        or hashlib.sha256(item.content.encode("utf-8")).hexdigest() == item.text_sha256
    ]


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
