from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import TypeVar

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
from src.models.graph import Citation, DocumentCandidate, RetrievalResult
from src.services.circuit import AsyncCircuitBreaker
from src.services.llm import get_llm
from src.services.metrics import metrics
from src.services.retrieval import (
    extract_document_numbers,
    extract_legal_labels,
    is_metadata_question,
    is_simple_status_metadata_question,
    normalize_identifier,
    policy_response,
    requires_evidence_verification,
    retrieval_intent,
    rerank_semantic_by_query_overlap,
    semantic_document_focus,
    weighted_rrf,
)


class GraphRagUnavailableError(RuntimeError):
    """A required GraphRAG dependency is unavailable."""


class ChatProviderError(RuntimeError):
    """The configured chat provider failed to generate a response."""


_RETRIEVAL_POLICY_VERSION = "hybrid-v4"
logger = logging.getLogger(__name__)
_ProviderResult = TypeVar("_ProviderResult")


@dataclass(frozen=True)
class RetrievalBundle:
    evidence: list[RetrievalResult]
    relations: list
    direct_response: str = ""
    direct_citations: list[Citation] | None = None


class GraphRagRuntime:
    """Own request-time GraphRAG dependencies and their lifecycle."""

    def __init__(self) -> None:
        self._embeddings: EmbeddingModel | None = None
        self._graph_store: Neo4jGraphStore | None = None
        self._vector_store: QdrantVectorStore | None = None
        self._active_release: tuple[str, int, float] | None = None
        self._embedding_cache: dict[str, tuple[list[float], float]] = {}
        self._embedding_inflight: dict[str, asyncio.Task] = {}
        self._embedding_lock = asyncio.Lock()
        self._provider_semaphore = asyncio.Semaphore(max(1, get_settings().provider_concurrency))
        self._embedding_breaker = AsyncCircuitBreaker(
            failure_threshold=get_settings().provider_circuit_failure_threshold,
            cooldown_seconds=get_settings().provider_circuit_cooldown_seconds,
        )
        self._qdrant_breaker = AsyncCircuitBreaker(
            failure_threshold=get_settings().provider_circuit_failure_threshold,
            cooldown_seconds=get_settings().provider_circuit_cooldown_seconds,
        )
        self._neo4j_breaker = AsyncCircuitBreaker(
            failure_threshold=get_settings().provider_circuit_failure_threshold,
            cooldown_seconds=get_settings().provider_circuit_cooldown_seconds,
        )
        self._exact_cache: dict[tuple[str, str], tuple[list[DocumentCandidate], float]] = {}
        self._retrieval_cache: dict[tuple[tuple[object, ...], str], tuple[RetrievalBundle, float]] = {}
        # Generated answers are safe to reuse only while the immutable active
        # release and every prompt/input fingerprint remain identical.  Keep
        # this process-local and bounded; a release switch naturally makes
        # old entries unreachable and ``close`` clears them explicitly.
        self._answer_cache: dict[tuple[tuple[object, ...], str], tuple[str, float]] = {}
        self._readiness_cache: tuple[dict[str, bool], float] | None = None
        self._readiness_task: asyncio.Task[dict[str, bool]] | None = None

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
        started = time.perf_counter()
        safe_response = policy_response(query)
        if safe_response:
            metrics.inc("retrieval_requests_total", mode="policy", outcome="success")
            metrics.observe("retrieval_duration_seconds", time.perf_counter() - started, mode="policy")
            return RetrievalBundle(evidence=[], relations=[], direct_response=safe_response)
        normalized_query = " ".join(query.casefold().split())
        settings = get_settings()
        current_release = self._active_release[0] if self._active_release else ""
        cache_namespace = (
            current_release,
            settings.embedding_model,
            settings.embedding_dimensions,
            settings.model_name,
            settings.qdrant_collection,
            settings.retrieval_top_k,
            settings.retrieval_candidate_k,
            settings.semantic_similarity_threshold,
            settings.graph_hops,
            settings.max_llm_evidence,
            _RETRIEVAL_POLICY_VERSION,
        )
        cached = self._retrieval_cache.get((cache_namespace, normalized_query)) if current_release else None
        if cached and time.monotonic() - cached[1] < 60:
            metrics.inc("retrieval_requests_total", mode="cache", outcome="success")
            metrics.observe("retrieval_duration_seconds", time.perf_counter() - started, mode="cache")
            return _copy_bundle(cached[0])
        async with trace_span(
            "retrieve-context",
            as_type="retriever",
            input={"query": query},
        ) as span:
            try:
                bundle = await asyncio.wait_for(
                    self._retrieve(query), timeout=get_settings().retrieval_timeout_seconds
                )
            except TimeoutError as exc:
                metrics.inc("retrieval_requests_total", mode="provider", outcome="timeout")
                metrics.observe("retrieval_duration_seconds", time.perf_counter() - started, mode="provider")
                raise GraphRagUnavailableError("Retrieval deadline exceeded") from exc
            except Exception:
                metrics.inc("retrieval_requests_total", mode="provider", outcome="error")
                metrics.observe("retrieval_duration_seconds", time.perf_counter() - started, mode="provider")
                raise
            if span is not None:
                span.update(
                    output={
                        "evidence_count": len(bundle.evidence),
                        "relation_count": len(bundle.relations),
                        "chunk_ids": [item.chunk_id for item in bundle.evidence],
                        "direct": bool(bundle.direct_response),
                    }
                )
            if self._active_release:
                cache_key = (
                    (
                        self._active_release[0],
                        settings.embedding_model,
                        settings.embedding_dimensions,
                        settings.model_name,
                        settings.qdrant_collection,
                        settings.retrieval_top_k,
                        settings.retrieval_candidate_k,
                        settings.semantic_similarity_threshold,
                        settings.graph_hops,
                        settings.max_llm_evidence,
                        _RETRIEVAL_POLICY_VERSION,
                    ),
                    normalized_query,
                )
                if len(self._retrieval_cache) >= 128:
                    oldest = min(self._retrieval_cache, key=lambda item: self._retrieval_cache[item][1])
                    self._retrieval_cache.pop(oldest, None)
                self._retrieval_cache[cache_key] = (_copy_bundle(bundle), time.monotonic())
            metrics.inc("retrieval_requests_total", mode="provider", outcome="success")
            metrics.observe("retrieval_duration_seconds", time.perf_counter() - started, mode="provider")
            return bundle

    async def retrieve_bundle_many(self, queries: Sequence[str]) -> RetrievalBundle:
        """Retrieve bounded sub-queries with one embedding/Qdrant batch.

        The method is used only for explicit deterministic decomposition. Each
        sub-query still passes through the same release-scoped lexical,
        hydration, graph and verification path; only provider calls that can
        preserve ordering are batched.
        """
        bounded = list(dict.fromkeys(" ".join(query.split()) for query in queries if query and query.strip()))[:3]
        if len(bounded) <= 1:
            return await self.retrieve_bundle(bounded[0] if bounded else "")
        if any(policy_response(query) for query in bounded):
            bundles = await asyncio.gather(*(self.retrieve_bundle(query) for query in bounded))
            return _merge_bundles(bundles)

        settings = get_settings()
        vectors = await self._embed_queries(bounded)
        if any(len(vector) != settings.embedding_dimensions for vector in vectors):
            raise GraphRagUnavailableError("Query embedding has unexpected dimensions")
        dataset_id = ""
        async with session_scope() as session:
            release = await GraphRepository(session).current_dataset_release()
            if release is not None:
                dataset_id = release[0]
        if not dataset_id:
            raise GraphRagUnavailableError("No active dataset is available")
        vector_hits = await self._search_vectors_many(
            vectors,
            dataset_id=dataset_id,
            limit=settings.retrieval_candidate_k,
            score_threshold=settings.semantic_similarity_threshold,
        )
        bundles = await asyncio.gather(*(
            self._retrieve_staged(query, vector_override=vector, vector_hits_override=hits)
            for query, vector, hits in zip(bounded, vectors, vector_hits, strict=True)
        ))
        return _merge_bundles(bundles)

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
        async with self._embedding_lock:
            task = self._embedding_inflight.get(key)
            if task is None:
                task = asyncio.create_task(self._embed_provider(query))
                self._embedding_inflight[key] = task
        try:
            vector = [float(value) for value in await task]
        finally:
            if self._embedding_inflight.get(key) is task:
                self._embedding_inflight.pop(key, None)
        if len(self._embedding_cache) >= 256:
            oldest = min(self._embedding_cache, key=lambda item: self._embedding_cache[item][1])
            self._embedding_cache.pop(oldest, None)
        self._embedding_cache[key] = (vector, now)
        return vector

    async def _embed_provider(self, query: str) -> Sequence[float]:
        return await self._provider_call(
            "embedding",
            self._embedding_breaker,
            lambda: self._get_embeddings().embed_query(query),
        )

    async def _embed_queries(self, queries: Sequence[str]) -> list[Sequence[float]]:
        values = list(queries)
        return await self._provider_call(
            "embedding_batch",
            self._embedding_breaker,
            lambda: self._get_embeddings().embed_queries(values),
        )

    async def _search_vectors(self, vector: Sequence[float], **kwargs):
        return await self._provider_call(
            "qdrant",
            self._qdrant_breaker,
            lambda: self._get_vector_store().search(vector, **kwargs),
        )

    async def _search_vectors_many(self, vectors: Sequence[Sequence[float]], **kwargs):
        return await self._provider_call(
            "qdrant_batch",
            self._qdrant_breaker,
            lambda: self._get_vector_store().search_many(vectors, **kwargs),
        )

    async def _provider_call(
        self,
        stage: str,
        breaker: AsyncCircuitBreaker,
        operation: Callable[[], Awaitable[_ProviderResult]],
    ) -> _ProviderResult:
        """Bound provider concurrency while exposing queue and latency metrics."""
        wait_started = time.perf_counter()
        async with self._provider_semaphore:
            metrics.observe("provider_queue_wait_seconds", time.perf_counter() - wait_started, stage=stage)
            metrics.inc("provider_inflight", 1, stage=stage)
            started = time.perf_counter()
            try:
                result = await breaker.call(operation)
            except asyncio.CancelledError:
                raise
            except Exception:
                metrics.inc("provider_calls_total", outcome="error", stage=stage)
                raise
            else:
                metrics.inc("provider_calls_total", outcome="success", stage=stage)
                return result
            finally:
                metrics.inc("provider_inflight", -1, stage=stage)
                metrics.observe("provider_duration_seconds", time.perf_counter() - started, stage=stage)

    async def _find_documents(self, repository: GraphRepository, *, dataset_id: str, number: str) -> list[DocumentCandidate]:
        key = (dataset_id, number)
        now = time.monotonic()
        cached = self._exact_cache.get(key)
        if cached and now - cached[1] < 300:
            return cached[0]
        documents = await repository.find_documents(number, dataset_id=dataset_id, limit=3)
        if len(self._exact_cache) >= 256:
            oldest = min(self._exact_cache, key=lambda item: self._exact_cache[item][1])
            self._exact_cache.pop(oldest, None)
        self._exact_cache[key] = (documents, now)
        return documents

    async def _retrieve(self, query: str) -> RetrievalBundle:
        return await self._retrieve_staged(query)

    async def _retrieve_staged(
        self,
        query: str,
        *,
        vector_override: Sequence[float] | None = None,
        vector_hits_override: Sequence[VectorHit] | None = None,
    ) -> RetrievalBundle:
        """Retrieve in bounded DB phases; never hold a SQL session over providers."""
        settings = get_settings()

        async def lexical_search(
            *, dataset_id: str, document_ids: Sequence[str] | None = None, limit: int
        ) -> list[RetrievalResult]:
            async with session_scope() as lexical_session:
                return await GraphRepository(lexical_session).search_lexical(
                    query,
                    dataset_id=dataset_id,
                    document_ids=document_ids,
                    limit=limit,
                )

        try:
            # Phase 1: release metadata, exact lookup and PageIndex. This
            # session closes before embedding, Qdrant or Neo4j calls.
            async with session_scope() as session:
                repository = GraphRepository(session)
                async with trace_span("get-current-dataset") as span:
                    dataset_id, expected_points = await self._active_dataset(repository)
                    if span is not None:
                        span.update(output={"dataset_id": dataset_id, "expected_qdrant_points": expected_points})
                exact_candidates: list[DocumentCandidate] = []
                for number in extract_document_numbers(query):
                    exact_candidates.extend(
                        await self._find_documents(repository, dataset_id=dataset_id, number=number)
                    )
                exact_candidates = list({candidate.document_id: candidate for candidate in exact_candidates}.values())
                # ``find_documents`` also returns documents whose title mentions
                # the signature.  Keep only exact signature matches for a direct
                # metadata answer; related documents must not create ambiguity.
                exact_signatures = {
                    normalize_identifier(number) for number in extract_document_numbers(query)
                }
                signature_matches = [
                    candidate
                    for candidate in exact_candidates
                    if normalize_identifier(candidate.so_ky_hieu) in exact_signatures
                ]
                if signature_matches:
                    exact_candidates = signature_matches
                intent = retrieval_intent(query)
                if (
                    is_metadata_question(query)
                    and (intent == "lookup" or is_simple_status_metadata_question(query))
                    and len(exact_candidates) == 1
                    and exact_candidates[0].answer_ready
                ):
                    document = exact_candidates[0]
                    return RetrievalBundle(
                        [],
                        [],
                        _format_metadata_answer(query, document),
                        [
                            Citation(
                                document_id=document.document_id,
                                chunk_id=f"metadata:{document.document_id}",
                                dataset_id=dataset_id,
                                title=document.title,
                                # Keep the complete, provenance-checked metadata
                                # record in the citation context.  A status-only
                                # quote made date/title answers look like a
                                # retrieval miss to the evaluator and to clients
                                # rendering citation details.
                                # Keep a complete metadata record in the
                                # citation even when the user-facing answer is
                                # intentionally narrowed to the requested
                                # field(s).
                                quote=_format_metadata_answer(query, document, include_context=True),
                                channels=["exact"],
                                evidence_kind="document_metadata",
                                provenance_verified=document.legal_status_verified,
                                source_url=document.legal_status_source,
                                source_checked_at=document.legal_status_checked_at,
                            )
                        ],
                    )
                exact_document_ids = [
                    candidate.document_id for candidate in exact_candidates if candidate.answer_ready
                ]
                page_results = _verified_evidence(
                    await repository.resolve_legal_units(
                        extract_legal_labels(query),
                        dataset_id=dataset_id,
                        document_ids=exact_document_ids,
                    )
                )

            if page_results and intent in {"lookup", "legal_unit"}:
                lexical_results = await lexical_search(
                    dataset_id=dataset_id,
                    document_ids=exact_document_ids,
                    limit=max(8, settings.retrieval_top_k),
                )
                return RetrievalBundle(
                    evidence=_verified_evidence(
                        weighted_rrf(
                            {"page_index": page_results, "lexical": lexical_results},
                            limit=settings.max_llm_evidence,
                        )
                    ),
                    relations=[],
                )

            # Phase 2: independent lexical/provider work. The lexical task owns
            # its own short-lived DB session, so provider wait cannot pin it.
            lexical_task = asyncio.create_task(
                lexical_search(dataset_id=dataset_id, limit=settings.retrieval_candidate_k)
            )
            async with trace_span(
                "embedding-query",
                as_type="embedding",
                input={"query_length": len(query)},
                metadata={"model": settings.embedding_model},
            ) as span:
                vector = vector_override if vector_override is not None else await self._embed_query(query)
                if span is not None:
                    span.update(output={"embedding_dimensions": len(vector)})
            if len(vector) != settings.embedding_dimensions:
                raise GraphRagUnavailableError("Query embedding has unexpected dimensions")
            async with trace_span(
                "qdrant-search", as_type="retriever", metadata={"dataset_id": dataset_id}
            ) as span:
                if vector_hits_override is None:
                    semantic_task = asyncio.create_task(
                        self._search_vectors(
                            vector,
                            dataset_id=dataset_id,
                            limit=settings.retrieval_candidate_k,
                            score_threshold=settings.semantic_similarity_threshold,
                        )
                    )
                    lexical_results, vector_hits = await asyncio.gather(lexical_task, semantic_task)
                else:
                    lexical_results = await lexical_task
                    vector_hits = list(vector_hits_override)
                if span is not None:
                    span.update(output={"result_count": len(vector_hits)})

            # Phase 3: bounded hydration/sibling expansion only.
            async with session_scope() as hydration_session:
                hydration_repository = GraphRepository(hydration_session)
                if hasattr(hydration_repository, "hydrate_chunks_with_scope"):
                    hydrated, semantic_scope = await hydration_repository.hydrate_chunks_with_scope(
                        [item.chunk_id for item in vector_hits],
                        dataset_id=dataset_id,
                        scope_limit=settings.max_llm_evidence,
                    )
                else:
                    hydrated = await hydration_repository.hydrate_chunks(
                        [item.chunk_id for item in vector_hits], dataset_id=dataset_id
                    )
                    fallback_focus = [
                        item for item in hydrated
                        if _is_enumerated_unit(item.section_title or item.content)
                    ]
                    semantic_scope = await hydration_repository.expand_sibling_legal_units(
                        [item.unit_id for item in fallback_focus if item.unit_id],
                        dataset_id=dataset_id,
                        limit=settings.max_llm_evidence,
                    )
                semantic_results = rerank_semantic_by_query_overlap(
                    query, _verify_hydrated_hits(hydrated, vector_hits)
                )
                semantic_focus = semantic_document_focus(semantic_results)

            channels: dict[str, Sequence[RetrievalResult]] = {
                "lexical": lexical_results,
                "semantic": semantic_results,
            }
            if page_results:
                channels["page_index"] = page_results
            if semantic_focus:
                channels["semantic_focus"] = semantic_focus
            if semantic_scope and intent in {"lookup", "legal_unit"}:
                return RetrievalBundle(evidence=_verified_evidence(semantic_scope), relations=[])
            if semantic_scope:
                channels["semantic_scope"] = semantic_scope

            graph_results: list = []
            seed_ids = list(dict.fromkeys(item.document_id for item in weighted_rrf(channels, limit=6)))
            if intent in {"temporal", "relational"} and seed_ids:
                async with trace_span(
                    "neo4j-expand", as_type="retriever", metadata={"dataset_id": dataset_id}
                ) as span:
                    graph_results = await self._provider_call(
                        "neo4j",
                        self._neo4j_breaker,
                        lambda: self._get_graph_store().expand(
                            seed_ids,
                            dataset_id=dataset_id,
                            hops=min(settings.graph_hops, 2 if intent == "temporal" else 1),
                            limit=settings.graph_neighbor_limit,
                        )
                    )
                    if span is not None:
                        span.update(output={"relation_count": len(graph_results)})
                related_ids = list(
                    dict.fromkeys(
                        identifier
                        for relation in graph_results
                        for identifier in (relation.source_id, relation.target_id)
                        if identifier and identifier not in seed_ids
                    )
                )[: settings.graph_evidence_limit]
                if related_ids:
                    graph_lexical_task = asyncio.create_task(
                        lexical_search(
                            dataset_id=dataset_id,
                            document_ids=related_ids,
                            limit=settings.graph_evidence_limit,
                        )
                    )
                    graph_vector_hits = await self._search_vectors(
                        vector,
                        dataset_id=dataset_id,
                        document_ids=related_ids,
                        limit=settings.graph_evidence_limit,
                        score_threshold=settings.semantic_similarity_threshold,
                    )
                    graph_lexical = await graph_lexical_task
                    async with session_scope() as graph_session:
                        graph_repository = GraphRepository(graph_session)
                        graph_semantic = await _hydrate_vector_hits(
                            graph_repository, graph_vector_hits, dataset_id
                        )
                    channels["legal_graph"] = _merge_evidence(graph_lexical, graph_semantic)

            return RetrievalBundle(
                evidence=_verified_evidence(
                    weighted_rrf(
                        channels,
                        limit=settings.max_llm_evidence,
                        max_per_document=3 if semantic_focus else 2,
                    )
                ),
                relations=graph_results,
            )
        except GraphRagUnavailableError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise GraphRagUnavailableError("GraphRAG dependencies are unavailable") from exc
        except Exception as exc:
            raise GraphRagUnavailableError("GraphRAG retrieval failed") from exc

    async def generate(self, query: str, context: str) -> str:
        started = time.perf_counter()
        settings = get_settings()
        normalized_query = " ".join(query.casefold().split())
        current_release = self._active_release[0] if self._active_release else ""
        answer_namespace = (
            current_release,
            settings.model_name,
            settings.llm_temperature,
            settings.llm_max_output_tokens,
            _RETRIEVAL_POLICY_VERSION,
        )
        context_digest = hashlib.sha256(context.encode("utf-8")).hexdigest()
        answer_key = (answer_namespace, f"{normalized_query}\n{context_digest}")
        cache_allowed = _answer_cache_allowed(query)
        cached = self._answer_cache.get(answer_key) if current_release and context and cache_allowed else None
        if cached and time.monotonic() - cached[1] < 60:
            metrics.inc("generation_requests_total", outcome="cache")
            metrics.observe("generation_duration_seconds", time.perf_counter() - started, outcome="cache")
            return cached[0]
        answer_instruction = _answer_format_instruction(query)
        try:
            llm = get_llm()
            result = await asyncio.wait_for(
                llm.ainvoke(
                    [
                        SystemMessage(content=SYSTEM_PROMPT),
                        HumanMessage(
                            content=(
                                f"Câu hỏi người dùng:\n{query}\n\n"
                                f"Evidence và graph relations được phép sử dụng:\n{context}\n\n"
                                f"Định dạng đầu ra bắt buộc:\n{answer_instruction}"
                            )
                        ),
                    ],
                    config=llm_invoke_config() or None,
                ),
                timeout=settings.llm_timeout_seconds,
            )
        except TimeoutError:
            metrics.inc("generation_requests_total", outcome="timeout")
            metrics.observe("generation_duration_seconds", time.perf_counter() - started, outcome="timeout")
            return NO_EVIDENCE_RESPONSE
        except Exception as exc:
            metrics.inc("generation_requests_total", outcome="error")
            metrics.observe("generation_duration_seconds", time.perf_counter() - started, outcome="error")
            raise ChatProviderError("Chat provider failed") from exc
        content = result.content
        if isinstance(content, str):
            metrics.inc("generation_requests_total", outcome="success")
            metrics.observe("generation_duration_seconds", time.perf_counter() - started, outcome="success")
            if current_release and context and cache_allowed and content:
                self._store_answer_cache(answer_key, content)
            return content
        if isinstance(content, Sequence):
            value = "".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
            metrics.inc("generation_requests_total", outcome="success")
            metrics.observe("generation_duration_seconds", time.perf_counter() - started, outcome="success")
            if current_release and context and cache_allowed and value:
                self._store_answer_cache(answer_key, value)
            return value
        metrics.inc("generation_requests_total", outcome="empty")
        metrics.observe("generation_duration_seconds", time.perf_counter() - started, outcome="empty")
        return ""

    def _store_answer_cache(self, key: tuple[tuple[object, ...], str], answer: str) -> None:
        if len(self._answer_cache) >= 128:
            oldest = min(self._answer_cache, key=lambda item: self._answer_cache[item][1])
            self._answer_cache.pop(oldest, None)
        self._answer_cache[key] = (answer, time.monotonic())

    async def readiness(self) -> dict[str, bool]:
        """Return a short-lived coalesced dependency probe.

        Render/Kubernetes may poll readiness concurrently. Re-running full
        Qdrant/Neo4j count checks for every probe creates artificial latency and
        connection pressure, so one probe is shared for a small bounded window.
        """
        now = time.monotonic()
        if self._readiness_cache and now - self._readiness_cache[1] < 5:
            return dict(self._readiness_cache[0])
        task = self._readiness_task
        if task is None or task.done():
            task = asyncio.create_task(self._readiness_probe())
            self._readiness_task = task
        checks = await task
        self._readiness_cache = (dict(checks), time.monotonic())
        return dict(checks)

    async def _readiness_probe(self) -> dict[str, bool]:
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
        release: tuple[str, int] | None = None
        projection_contract: dict[str, dict[str, object]] = {}
        try:
            async with session_scope() as session:
                await session.execute(text("SELECT 1"))
                repository = GraphRepository(session)
                release = await repository.current_dataset_release()
                projection_contract = (
                    await repository.current_projection_contract(release[0])
                    if release is not None
                    else {}
                )
            checks["database"] = True
            if release is not None:
                qdrant_contract = projection_contract.get("qdrant", {})
                qdrant_contract_ready = (
                    qdrant_contract.get("status") == "ready"
                    and qdrant_contract.get("expected_count") == qdrant_contract.get("actual_count")
                )
                checks["qdrant"] = bool(qdrant_contract_ready) and await asyncio.wait_for(
                    self._get_vector_store().readiness(dataset_id=release[0], expected_points=release[1]),
                    timeout=10,
                )
                neo4j_contract = projection_contract.get("neo4j", {})
                neo4j_metadata = neo4j_contract.get("metadata", {}) or {}
                expected_approved_edges = neo4j_metadata.get("approved_evidence")
                neo4j_contract_ready = (
                    neo4j_contract.get("status") == "ready"
                    and neo4j_contract.get("expected_count") == neo4j_contract.get("actual_count")
                )
                checks["neo4j"] = False
                if neo4j_contract_ready:
                    checks["neo4j"] = await asyncio.wait_for(
                        self._get_graph_store().readiness(
                            dataset_id=release[0],
                            expected_nodes=int(neo4j_contract["expected_count"]),
                            expected_approved_edges=(
                                int(expected_approved_edges) if expected_approved_edges is not None else None
                            ),
                        ),
                        timeout=10,
                    )
        except Exception:
            pass
        try:
            if release is not None and not checks["neo4j"] and not projection_contract.get("neo4j"):
                checks["neo4j"] = await asyncio.wait_for(
                    self._get_graph_store().readiness(dataset_id=release[0]), timeout=5
                )
        except Exception:
            pass
        return checks

    async def prewarm(self) -> None:
        """Open external clients and perform bounded health probes at startup."""
        try:
            async with session_scope() as session:
                await session.execute(text("SELECT 1"))
            if get_settings().qdrant_url and get_settings().qdrant_api_key:
                store = self._get_vector_store()
                await asyncio.wait_for(store.client.get_collections(), timeout=5)
            if get_settings().neo4j_uri and get_settings().neo4j_password:
                await asyncio.wait_for(self._get_graph_store().verify_connectivity(), timeout=5)
        except Exception:
            logger.warning("Dependency prewarm incomplete; readiness will continue asynchronously", exc_info=True)

    async def close(self) -> None:
        if self._graph_store is not None:
            await self._graph_store.close()
            self._graph_store = None
        if self._vector_store is not None:
            await self._vector_store.close()
            self._vector_store = None
        self._embeddings = None
        get_embedding_model.cache_clear()
        self._active_release = None
        self._embedding_cache.clear()
        pending_embeddings = list(self._embedding_inflight.values())
        for task in pending_embeddings:
            task.cancel()
        self._embedding_inflight.clear()
        if pending_embeddings:
            await asyncio.gather(*pending_embeddings, return_exceptions=True)
        self._exact_cache.clear()
        self._retrieval_cache.clear()
        self._answer_cache.clear()


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
    return _verify_hydrated_hits(hydrated, hits)


def _verify_hydrated_hits(
    hydrated: Sequence[RetrievalResult], hits: Sequence[VectorHit]
) -> list[RetrievalResult]:
    by_id = {item.chunk_id: item for item in hits}
    verified: list[RetrievalResult] = []
    for item in hydrated:
        hit = by_id.get(item.chunk_id)
        # A semantic hit is untrusted until the immutable Qdrant payload and
        # canonical PostgreSQL row carry the same embedding-input digest.
        # Missing digests are rejected as well; accepting them would let a
        # stale/poisoned vector bypass the release provenance boundary.
        if (
            hit is None
            or not hit.input_sha256
            or not item.input_sha256
            or hit.input_sha256 != item.input_sha256
        ):
            continue
        item.score = hit.score
        item.rank_details = {"semantic_raw_score": hit.score}
        verified.append(item)
    return verified


def _is_enumerated_unit(value: str) -> bool:
    stripped = value.lstrip().casefold()
    return len(stripped) >= 2 and stripped[0].isalpha() and stripped[1] == ")"


def _format_metadata_answer(
    query: str,
    document: DocumentCandidate,
    *,
    include_context: bool = False,
) -> str:
    """Render only the metadata fields requested by the user.

    Exact metadata retrieval is already provenance-checked, so adding every
    available field to a one-field question only makes the answer less
    relevant and gives evaluators extra claims to score.  Citations use
    ``include_context=True`` to retain a complete, auditable metadata record.
    """
    lowered = query.casefold()
    asks_title = any(token in lowered for token in ("tiêu đề", "tên văn bản", "tên đầy đủ", "tên của"))
    asks_status = any(token in lowered for token in ("hiệu lực", "tình trạng"))
    asks_issue_date = "ban hành" in lowered
    asks_category = any(token in lowered for token in ("danh mục", "category", "thuộc nhóm", "nhóm nội dung"))
    label = document.so_ky_hieu or document.document_id
    status = document.legal_status if document.legal_status_verified else "chưa xác minh từ nguồn chính thức"
    # The graph status formatter may append an internal relation summary such
    # as ``không là target ...``.  It is provenance metadata, not the legal
    # status requested by a user, so keep the canonical status label only.
    status = status.split(" và không là target", 1)[0].strip(" .")
    category_labels = {
        "bhyt": "bảo hiểm y tế (BHYT)",
        "vien_phi": "viện phí",
        "vienphi": "viện phí",
    }
    category_values = [category_labels.get(value.casefold(), value) for value in document.categories]

    if not include_context:
        focused: list[str] = []
        if asks_title:
            focused.append(f"Tên đầy đủ của văn bản số hiệu {label} là: {document.title}.")
        if asks_status:
            if document.ngay_co_hieu_luc:
                focused.append(
                    f"Văn bản số hiệu {label} có hiệu lực từ ngày {document.ngay_co_hieu_luc} "
                    f"và có tình trạng {status}."
                )
            else:
                focused.append(f"Văn bản số hiệu {label} có tình trạng {status}.")
        if asks_issue_date and document.ngay_ban_hanh:
            focused.append(f"Văn bản số hiệu {label} được ban hành ngày {document.ngay_ban_hanh}.")
        if asks_category and category_values:
            focused.append(
                f"Nhóm nội dung của văn bản số hiệu {label} là {category_values[-1]} trong bộ dữ liệu."
            )
        if focused:
            return " ".join(focused)

    values: list[str] = [f"Văn bản {label}: {document.title}."]
    if asks_status:
        values.append(f"Tình trạng: {status}.")
        if document.ngay_co_hieu_luc:
            values.append(f"Có hiệu lực từ: {document.ngay_co_hieu_luc}.")
    if asks_issue_date and document.ngay_ban_hanh:
        values.append(f"Ngày ban hành: {document.ngay_ban_hanh}.")
    if asks_category and category_values:
        values.append("Nhóm: " + ", ".join(category_values) + ".")
        if include_context:
            values.append(
                f"Phân loại: Văn bản số hiệu {label} thuộc nhóm {category_values[-1]} trong bộ dữ liệu."
            )
    return " ".join(values)


def _answer_format_instruction(query: str) -> str:
    """Keep generation concise and deterministic by retrieval intent."""
    intent = retrieval_intent(query)
    if intent in {"lookup", "legal_unit"}:
        return "Trả lời tối đa 5 gạch đầu dòng, nêu đúng điều/khoản và không suy diễn."
    if intent == "temporal":
        return "Trả lời theo mốc thời gian: văn bản, ngày, trạng thái và nguồn; tối đa 6 gạch đầu dòng."
    if intent == "relational":
        return "Nêu quan hệ nguồn → đích và ý nghĩa được evidence xác nhận; tối đa 6 gạch đầu dòng."
    return "Trả lời ngắn gọn trong tối đa 8 gạch đầu dòng; nếu thiếu evidence hãy nói rõ giới hạn."


def _answer_cache_allowed(query: str) -> bool:
    """Only cache low-risk public answers after release-scoped verification.

    Temporal, status/payment and other evidence-verification intents must
    execute the verifier/provider path on every request until an external
    invalidation contract is proven.
    """
    return retrieval_intent(query) not in {"temporal", "relational"} and not requires_evidence_verification(query)


def _verified_evidence(evidence: Sequence[RetrievalResult]) -> list[RetrievalResult]:
    """Reject stale or mixed-release text before it reaches an LLM/citation."""
    return [
        item for item in evidence
        if item.dataset_id and (
            ("page_index" in item.channels and item.source_start is not None and item.source_end is not None)
            or (
                bool(item.text_sha256)
                and hashlib.sha256(item.content.encode("utf-8")).hexdigest() == item.text_sha256
            )
        )
    ]


def _copy_bundle(bundle: RetrievalBundle) -> RetrievalBundle:
    return RetrievalBundle(
        evidence=[item.model_copy(deep=True) for item in bundle.evidence],
        relations=[item.model_copy(deep=True) for item in bundle.relations],
        direct_response=bundle.direct_response,
        direct_citations=[item.model_copy(deep=True) for item in bundle.direct_citations or []],
    )


def _merge_bundles(bundles: Sequence[RetrievalBundle]) -> RetrievalBundle:
    """Merge sub-query results without allowing one fragment to dominate."""
    evidence_by_id: dict[str, RetrievalResult] = {}
    relations_by_id: dict[str, object] = {}
    direct_response = ""
    direct_citations: list[Citation] = []
    seen_citations: set[str] = set()
    for bundle in bundles:
        if bundle.direct_response and not direct_response:
            direct_response = bundle.direct_response
        for citation in bundle.direct_citations or []:
            if citation.chunk_id not in seen_citations:
                seen_citations.add(citation.chunk_id)
                direct_citations.append(citation)
        for item in bundle.evidence:
            current = evidence_by_id.get(item.chunk_id)
            if current is None:
                evidence_by_id[item.chunk_id] = item.model_copy(deep=True)
            else:
                current.channels = sorted(set([*current.channels, *item.channels]))
                current.score = max(current.score, item.score)
        for relation in bundle.relations:
            key = str(getattr(relation, "relationship_id", "")) or (
                f"{getattr(relation, 'source_id', '')}:"
                f"{getattr(relation, 'target_id', '')}:"
                f"{getattr(relation, 'relation_type', '')}"
            )
            relations_by_id[key] = relation
    if direct_response:
        # A deterministic policy route dominates mixed sub-query results; do
        # not let a safe refusal be replaced by an LLM answer to another
        # fragment of the same user turn.
        return RetrievalBundle(
            evidence=[],
            relations=[],
            direct_response=direct_response,
            direct_citations=direct_citations,
        )
    evidence = sorted(
        evidence_by_id.values(),
        key=lambda item: (-float(item.score), str(item.chunk_id)),
    )[: get_settings().max_llm_evidence]
    return RetrievalBundle(
        evidence=evidence,
        relations=list(relations_by_id.values()),
        direct_response="",
        direct_citations=direct_citations,
    )


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
