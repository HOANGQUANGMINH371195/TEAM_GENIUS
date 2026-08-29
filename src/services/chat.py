from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import text

from src.agents.prompts import NO_EVIDENCE_RESPONSE, SYSTEM_PROMPT
from src.config import get_settings
from src.db.repositories import GraphRepository
from src.db.session import dispose_database, session_scope
from src.domain.route_plan import build_route_plan
from src.integrations.embeddings import EmbeddingModel, get_embedding_model
from src.integrations.langfuse import llm_invoke_config, resolve_prompt, trace_span
from src.integrations.neo4j import Neo4jGraphStore
from src.integrations.qdrant import QdrantVectorStore, VectorHit
from src.models.graph import Citation, DocumentCandidate, RetrievalResult
from src.models.schemas import GroundedAnswer
from src.services.circuit import AsyncCircuitBreaker
from src.services.experience_retrieval import ExperienceIndex
from src.services.global_retrieval import CommunitySummary, drift_search
from src.services.llm import get_llm
from src.services.metrics import metrics
from src.services.query_rewrite import rewrite_retrieval_query, should_rewrite_query
from src.services.retrieval import (
    exclude_unverified_legacy_subordinate_sources,
    extract_document_numbers,
    extract_internal_legal_references,
    extract_legal_labels,
    extract_query_phrases,
    extract_query_terms,
    filter_current_authority_candidates,
    filter_relations_by_query,
    is_metadata_question,
    is_simple_status_metadata_question,
    normalize_identifier,
    policy_response,
    requires_clause_expansion,
    requires_evidence_verification,
    rerank_legal_candidates,
    retrieval_intent,
    scope_evidence_matches_query,
    weighted_rrf,
)


class GraphRagUnavailableError(RuntimeError):
    """A required GraphRAG dependency is unavailable."""


class ChatProviderError(RuntimeError):
    """The configured chat provider failed to generate a response."""


# Bump whenever ranking/selection semantics change.  This is part of each
# in-process cache namespace, so an answer produced before the domain/scope
# policy cannot be replayed after a rolling deployment.
_RETRIEVAL_POLICY_VERSION = "hybrid-v14-no-domain-anchor-mapping"
logger = logging.getLogger(__name__)
_ProviderResult = TypeVar("_ProviderResult")
_trace_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "medipay_trace_context", default=None
)
_generation_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "medipay_generation_context", default=None
)


def _record_trace_event(
    stage: str,
    started: float,
    *,
    outcome: str = "success",
    **details: object,
) -> None:
    """Record bounded, secret-free stage timing for local eval and ops.

    This deliberately does not capture prompts, document IDs, provider
    payloads, or credentials.  It remains useful when Langfuse is disabled or
    unavailable, and the ContextVar keeps concurrent requests isolated.
    """
    trace = _trace_context.get()
    if trace is None:
        return
    event: dict[str, object] = {
        "stage": stage,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "outcome": outcome,
    }
    for key, value in details.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            event[key] = value
    events = trace.setdefault("stages", [])
    if isinstance(events, list) and len(events) < 128:
        events.append(event)


@dataclass(frozen=True)
class RetrievalBundle:
    evidence: list[RetrievalResult]
    relations: list
    direct_response: str = ""
    direct_citations: list[Citation] | None = None
    trace: dict[str, Any] = dataclass_field(default_factory=dict)


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
        self._rewrite_breaker = AsyncCircuitBreaker(
            failure_threshold=get_settings().provider_circuit_failure_threshold,
            cooldown_seconds=get_settings().provider_circuit_cooldown_seconds,
        )
        self._exact_cache: dict[tuple[str, str], tuple[list[DocumentCandidate], float]] = {}
        self._retrieval_cache: dict[tuple[tuple[object, ...], str], tuple[RetrievalBundle, float]] = {}
        self._rewrite_cache: dict[tuple[str, str], tuple[str, float]] = {}
        # Generated answers are safe to reuse only while the immutable active
        # release and every prompt/input fingerprint remain identical.  Keep
        # this process-local and bounded; a release switch naturally makes
        # old entries unreachable and ``close`` clears them explicitly.
        self._answer_cache: dict[tuple[tuple[object, ...], str], tuple[str, float]] = {}
        self._readiness_cache: tuple[dict[str, bool], float] | None = None
        self._readiness_task: asyncio.Task[dict[str, bool]] | None = None
        self._community_index_cache: tuple[str, int, str, tuple[CommunitySummary, ...]] | None = None
        self._experience_index_cache: tuple[str, int, str, ExperienceIndex] | None = None
        self._authority_document_cache: dict[str, tuple[list[str], float]] = {}
        # Concurrent high-risk requests should share the release-scoped
        # authority seed instead of stampeding managed PostgreSQL with the
        # same metadata scan.  The lock is intentionally narrow and only
        # protects this bounded candidate lookup; passage retrieval remains
        # concurrent.
        self._authority_document_lock = asyncio.Lock()

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

    def generation_trace(self) -> dict[str, Any]:
        """Return the current task's secret-free provider usage snapshot."""
        return dict(_generation_context.get() or {})

    async def retrieve(self, query: str) -> tuple[list, list]:
        bundle = await self.retrieve_bundle(query)
        return bundle.evidence, bundle.relations

    async def document_relations(
        self,
        document_ids: Sequence[str],
        *,
        dataset_id: str,
        hops: int = 2,
        limit: int = 40,
    ) -> list:
        """Return a bounded release graph walk for public timeline hydration.

        The caller must hydrate both endpoints from canonical PostgreSQL before
        exposing anything. This method reuses the runtime's driver, semaphore
        and circuit breaker so the product endpoint cannot create one Neo4j
        connection pool per request.
        """
        ids = list(dict.fromkeys(str(value) for value in document_ids if value))[:8]
        if not ids or not dataset_id or not get_settings().feature_graph_enabled:
            return []
        return await self._provider_call(
            "neo4j_timeline",
            self._neo4j_breaker,
            lambda: self._get_graph_store().expand(
                ids,
                dataset_id=dataset_id,
                hops=max(1, min(int(hops), 2)),
                limit=max(1, min(int(limit), 100)),
            ),
        )

    async def retrieve_bundle_adaptive(self, query: str) -> RetrievalBundle:
        """Retrieve the original and a constrained rewrite concurrently.

        The original wording is always retained. Rewrite failure, timeout or
        rejection therefore cannot turn an otherwise answerable request into
        an outage, while the two result lists are fused by rank rather than by
        incomparable provider scores.
        """
        settings = get_settings()
        if not settings.query_rewrite_enabled or not should_rewrite_query(query):
            return await self.retrieve_bundle(query)
        # Long thematic questions already carry their decisive legal terms;
        # running a second full retrieval view adds ~10–15s while rarely
        # improving recall. Keep HyDE for temporal/relational routing (where
        # formal wording is genuinely different) and for short open queries
        # covered by the focused unit tests.
        if retrieval_intent(query) == "thematic" and len(query.split()) >= 10:
            return await self.retrieve_bundle(query)

        original_task = asyncio.create_task(self.retrieve_bundle(query))
        try:
            rewritten = await asyncio.wait_for(
                self._rewrite_query(query),
                timeout=settings.query_rewrite_timeout_seconds,
            )
        except Exception:
            metrics.inc("query_rewrite_total", outcome="fallback")
            return await original_task

        if " ".join(rewritten.casefold().split()) == " ".join(query.casefold().split()):
            metrics.inc("query_rewrite_total", outcome="unchanged")
            return await original_task

        rewritten_task = asyncio.create_task(self.retrieve_bundle(rewritten))
        original, expanded = await asyncio.gather(
            original_task,
            rewritten_task,
            return_exceptions=True,
        )
        valid = [item for item in (original, expanded) if isinstance(item, RetrievalBundle)]
        if len(valid) < 2:
            # A cancelled asyncpg operation can leave a pooled connection in
            # an uncertain transaction state. Reset the local pool only after
            # both adaptive branches have finished, so a timed-out branch
            # cannot poison the next benchmark/request.
            await dispose_database()
        if not valid:
            first_error = original if isinstance(original, BaseException) else expanded
            raise first_error
        metrics.inc("query_rewrite_total", outcome="success")
        if len(valid) == 1:
            return _copy_bundle(valid[0])
        merged = _merge_bundles(
            [original, expanded],
            channel_weights={"query_0": 1.0, "query_1": 1.2},
            # Keep a wider candidate pool until the legal ranker has seen
            # both phrasings.  Cutting to the final context size here let a
            # duplicated generic match evict a current operative clause that
            # occurred in only one query view.
            limit=min(
                getattr(settings, "retrieval_candidate_k", settings.max_llm_evidence * 4),
                settings.max_llm_evidence * 4,
            ),
            max_per_document=(
                max(settings.max_chunks_per_document, 6)
                if requires_clause_expansion(query)
                else settings.max_chunks_per_document
            ),
        )
        # RRF rewards an item that appears in both views. That is normally
        # desirable, but an older paraphrase can otherwise beat an operative
        # current law that the clause-shaped rewrite found only once. Reapply
        # the same source-authority/currentness policy after fusion; it does
        # not add documents or facts, only orders the already verified set.
        # RRF is intentionally consensus-biased.  A useful HyDE view may
        # surface one decisive operative clause that the original colloquial
        # wording cannot retrieve, so retain the best source-derived anchors
        # from *each* view before the final legal ranking.  This is bounded
        # and query-agnostic; it does not map a question to a document.
        anchors = [
            item
            for bundle in valid
            for item in rerank_legal_candidates(query, bundle.evidence)[:3]
        ]
        candidates = {item.chunk_id: item for item in merged.evidence}
        for anchor in anchors:
            if anchor.chunk_id not in candidates:
                candidates[anchor.chunk_id] = anchor
        ranked = rerank_legal_candidates(query, list(candidates.values()))
        # Preserve query-derived exact anchors from either adaptive view ahead
        # of the final context cut.  RRF/reranking can otherwise drop a
        # one-view legal-unit hit even though it is the only passage carrying
        # the user's distinctive three-token phrase.
        anchor_phrases = [
            phrase for phrase in extract_query_phrases(query, limit=16)
            if len(phrase.split()) >= 2
        ]
        if anchor_phrases:
            exact = [
                item
                for bundle in valid
                for item in bundle.evidence
                if any(
                    phrase.casefold() in f"{item.section_title} {item.content}".casefold()
                    for phrase in anchor_phrases
                )
            ]
            preserved: list[RetrievalResult] = []
            seen: set[str] = set()
            for item in exact:
                if item.chunk_id not in seen:
                    seen.add(item.chunk_id)
                    preserved.append(item)
                if len(preserved) >= min(3, settings.max_llm_evidence):
                    break
            if preserved:
                ranked = preserved + [item for item in ranked if item.chunk_id not in seen]
        if requires_evidence_verification(query) and not extract_document_numbers(query):
            # Adaptive retrieval must use the same generic currentness policy
            # as the primary route.  Never privilege a domain/category label
            # or a hand-written legal phrase here; the candidate metadata and
            # query-derived scope policy determine the order.
            current_items = filter_current_authority_candidates(query, ranked)
            if current_items:
                ranked = current_items
        return RetrievalBundle(
            evidence=ranked[: settings.max_llm_evidence],
            relations=merged.relations,
            direct_response=merged.direct_response,
            direct_citations=merged.direct_citations,
        )

    async def _rewrite_query(self, query: str) -> str:
        settings = get_settings()
        key = (settings.model_name, " ".join(query.casefold().split()))
        now = time.monotonic()
        cached = self._rewrite_cache.get(key)
        if cached and now - cached[1] < 300:
            return cached[0]
        rewritten = await self._provider_call(
            "query_rewrite",
            self._rewrite_breaker,
            lambda: rewrite_retrieval_query(query),
        )
        if len(self._rewrite_cache) >= 256:
            oldest = min(self._rewrite_cache, key=lambda item: self._rewrite_cache[item][1])
            self._rewrite_cache.pop(oldest, None)
        self._rewrite_cache[key] = (rewritten, now)
        return rewritten

    async def retrieve_bundle(self, query: str) -> RetrievalBundle:
        """Retrieve one request and attach an isolated local trace."""
        trace: dict[str, Any] = {"trace_id": uuid4().hex, "stages": []}
        token = _trace_context.set(trace)
        started = time.perf_counter()
        try:
            bundle = await self._retrieve_bundle(query)
            _record_trace_event("retrieval_total", started, evidence_count=len(bundle.evidence))
            return RetrievalBundle(
                evidence=bundle.evidence,
                relations=bundle.relations,
                direct_response=bundle.direct_response,
                direct_citations=bundle.direct_citations,
                trace={"trace_id": trace["trace_id"], "stages": list(trace["stages"])},
            )
        except Exception as exc:
            _record_trace_event("retrieval_total", started, outcome=type(exc).__name__)
            setattr(
                exc,
                "medipay_trace",
                {"trace_id": trace["trace_id"], "stages": list(trace["stages"])},
            )
            raise
        finally:
            _trace_context.reset(token)

    async def _retrieve_bundle(self, query: str) -> RetrievalBundle:
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
                    self._retrieve(query),
                    timeout=get_settings().retrieval_timeout_seconds,
                )
            except TimeoutError as exc:
                metrics.inc("retrieval_requests_total", mode="provider", outcome="timeout")
                metrics.observe("retrieval_duration_seconds", time.perf_counter() - started, mode="provider")
                await dispose_database()
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
            query_texts=bounded,
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

    async def _current_authority_ids(
        self, repository: GraphRepository, *, query: str, dataset_id: str, limit: int
    ) -> list[str]:
        """Return a cached current-authority seed without a DB stampede."""
        cached = self._authority_document_cache.get(dataset_id)
        if cached and time.monotonic() - cached[1] < 300:
            return list(cached[0])
        async with self._authority_document_lock:
            cached = self._authority_document_cache.get(dataset_id)
            if cached and time.monotonic() - cached[1] < 300:
                return list(cached[0])
            ids = await repository.current_authority_document_ids(
                query, dataset_id=dataset_id, limit=limit
            )
            self._authority_document_cache[dataset_id] = (list(ids), time.monotonic())
            return list(ids)

    async def _resolve_qdrant_release(
        self,
        *,
        dataset_id: str,
        expected_points: int,
        preferred_collection: str | None = None,
    ) -> None:
        """Align the vector adapter with the active PostgreSQL release.

        The resolver is read-only and bounded; it repairs stale environment
        aliases in memory without changing Qdrant or the release pointer.
        """
        settings = get_settings()
        if settings.app_env == "test" or not settings.qdrant_url or not settings.qdrant_api_key:
            return
        store = self._get_vector_store()
        resolved = await store.resolve_collection(
            dataset_id=dataset_id,
            expected_points=expected_points,
            preferred_collection=preferred_collection,
        )
        if not resolved:
            logger.warning(
                "No Qdrant collection matched active release",
                extra={"dataset_id": dataset_id, "expected_points": expected_points},
            )

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
            outcome = "success"
            try:
                result = await breaker.call(operation)
            except asyncio.CancelledError:
                outcome = "cancelled"
                raise
            except Exception:
                outcome = "error"
                metrics.inc("provider_calls_total", outcome="error", stage=stage)
                raise
            else:
                metrics.inc("provider_calls_total", outcome="success", stage=stage)
                return result
            finally:
                metrics.inc("provider_inflight", -1, stage=stage)
                metrics.observe("provider_duration_seconds", time.perf_counter() - started, stage=stage)
                _record_trace_event(f"provider:{stage}", started, outcome=outcome)

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

    def _load_community_summaries(self, *, release_id: str) -> tuple[CommunitySummary, ...]:
        """Load an optional immutable community index for one active release.

        The index is a navigation accelerator only. Invalid, mixed-release,
        or absent files fail closed to the normal hybrid route; its text is
        never returned as evidence without PostgreSQL hydration.
        """
        settings = get_settings()
        if not settings.feature_global_search_enabled or not settings.community_index_path:
            return ()
        path = Path(settings.community_index_path)
        try:
            stat = path.stat()
        except OSError:
            metrics.inc("global_index_load_total", outcome="missing")
            return ()
        cache = self._community_index_cache
        cache_key = (str(path), stat.st_mtime_ns, release_id)
        if cache and cache[:3] == cache_key:
            return cache[3]
        try:
            summaries: list[CommunitySummary] = []
            manifest_seen = False
            manifest_count: int | None = None
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("community index row must be an object")
                if record.get("index") == "community-summary-v1":
                    manifest_seen = True
                    if str(record.get("release_id") or "") != release_id:
                        raise ValueError("community index release mismatch")
                    try:
                        manifest_count = int(record.get("communities"))
                    except (TypeError, ValueError):
                        raise ValueError("community index manifest count is invalid") from None
                    continue
                if str(record.get("release_id") or "") != release_id:
                    raise ValueError("community summary release mismatch")
                summary = CommunitySummary(
                    community_id=str(record.get("community_id") or ""),
                    release_id=str(record.get("release_id") or ""),
                    title=str(record.get("title") or ""),
                    document_ids=tuple(str(value) for value in (record.get("document_ids") or [])),
                    text=str(record.get("text") or ""),
                    source_passage_ids=tuple(
                        str(value) for value in (record.get("source_passage_ids") or [])
                    ),
                    content_sha256=str(record.get("content_sha256") or ""),
                )
                summary.validate()
                summaries.append(summary)
            if not manifest_seen or not summaries or manifest_count != len(summaries):
                raise ValueError("community index is missing manifest or summaries")
            loaded = tuple(summaries)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            metrics.inc("global_index_load_total", outcome="invalid")
            self._community_index_cache = None
            return ()
        self._community_index_cache = (*cache_key, loaded)
        metrics.inc("global_index_load_total", outcome="success", communities=len(loaded))
        return loaded

    def _global_document_ids(self, query: str, *, release_id: str) -> list[str]:
        """Return bounded document seeds from the optional DRIFT selector."""
        settings = get_settings()
        summaries = self._load_community_summaries(release_id=release_id)
        hits = drift_search(
            query,
            summaries,
            max_hits=settings.global_max_communities,
            max_rounds=settings.global_max_rounds,
        )
        document_ids = list(
            dict.fromkeys(document_id for hit in hits for document_id in hit.document_ids)
        )
        return document_ids[: settings.retrieval_candidate_k]

    def experience_hints(self, query: str, *, release_id: str) -> list[dict[str, object]]:
        """Return reviewed workflow hints without making them legal evidence."""
        settings = get_settings()
        if not settings.feature_experience_retrieval_enabled or not settings.experience_index_path:
            return []
        path = Path(settings.experience_index_path)
        try:
            stat = path.stat()
            cache_key = (str(path), stat.st_mtime_ns, release_id)
            cached = self._experience_index_cache
            if cached and cached[:3] == cache_key:
                index = cached[3]
            else:
                index = ExperienceIndex.load(path, release_id=release_id)
                self._experience_index_cache = (*cache_key, index)
            hints = index.search(query, max_hits=3)
            metrics.inc("experience_index_load_total", outcome="success", hits=len(hints))
            return hints
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            self._experience_index_cache = None
            metrics.inc("experience_index_load_total", outcome="invalid")
            return []

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
        route_plan = build_route_plan(query, settings=settings)
        is_table_route = route_plan.route == "table"
        route_started = time.perf_counter()
        route_deadline = route_started + route_plan.retrieval_budget_ms / 1000

        async def bounded_db(awaitable, stage: str):
            """Enforce the route deadline for every phase-3 SQL operation.

            Provider calls already use the remaining route budget, but a
            managed-Postgres query can otherwise run past it (especially the
            document-bounded operative scan) and make the advertised latency
            budget meaningless.  Cancellation is fail-open: callers decide
            whether an optional channel should be dropped or lexical fallback
            should be returned.
            """
            remaining = route_deadline - time.perf_counter()
            if remaining <= 0:
                close = getattr(awaitable, "close", None)
                if callable(close):
                    close()
                _record_trace_event(f"postgres:{stage}", route_started, outcome="deadline")
                raise TimeoutError(f"retrieval route deadline exceeded at {stage}")
            try:
                return await asyncio.wait_for(awaitable, timeout=max(0.05, remaining))
            except TimeoutError:
                _record_trace_event(f"postgres:{stage}", route_started, outcome="deadline")
                raise

        async def lexical_search(
            *, dataset_id: str, document_ids: Sequence[str] | None = None, limit: int
        ) -> list[RetrievalResult]:
            started = time.perf_counter()
            try:
                async with session_scope() as lexical_session:
                    result = await GraphRepository(lexical_session).search_lexical(
                        query, dataset_id=dataset_id, document_ids=document_ids, limit=limit
                    )
                _record_trace_event("postgres:lexical", started, result_count=len(result))
                return result
            except Exception as exc:
                _record_trace_event("postgres:lexical", started, outcome=type(exc).__name__)
                raise

        try:
            phase1_started = time.perf_counter()
            qdrant_locator = ""
            # Phase 1: release metadata, exact lookup and PageIndex. This
            # session closes before embedding, Qdrant or Neo4j calls.
            async with session_scope() as session:
                repository = GraphRepository(session)
                async with trace_span("get-current-dataset") as span:
                    dataset_id, expected_points = await self._active_dataset(repository)
                    if span is not None:
                        span.update(output={"dataset_id": dataset_id, "expected_qdrant_points": expected_points})
                # The projection row is the authoritative physical locator.
                # Passing it to Qdrant avoids a collection-list/count scan on
                # every fresh process while retaining discovery as a safe
                # compatibility fallback for older releases.
                try:
                    if hasattr(repository, "current_projection_contract"):
                        projection = await repository.current_projection_contract(dataset_id)
                        qdrant_locator = str(
                            (projection.get("qdrant") or {}).get("locator") or ""
                        ).strip()
                except Exception:
                    qdrant_locator = ""
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
                global_document_ids: list[str] = []
                if route_plan.route == "global" and settings.feature_global_search_enabled:
                    global_started = time.perf_counter()
                    global_document_ids = self._global_document_ids(query, release_id=dataset_id)
                    _record_trace_event(
                        "community:drift_select",
                        global_started,
                        result_count=len(global_document_ids),
                    )
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
                                document_number=document.so_ky_hieu,
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
                # A rewrite can expand an abbreviation into the formal subject
                # found in a statute title.  Keep title hits as a tiny
                # *candidate* set only; no title ever becomes public evidence
                # without a matching canonical passage below.
                document_recall_enabled = (
                    not exact_document_ids
                    and not is_metadata_question(query)
                    # Numeric routes still need document-level recall: the
                    # governing statute often uses a different formulation
                    # from the user's wording (for example "tuyến tỉnh" vs
                    # a formal technical level).  The pass is bounded and
                    # remains candidate-only; final evidence is re-ranked
                    # from canonical passages.
                    # High-risk entitlement questions already receive the
                    # canonical lexical + dense passage cascade below. The
                    # document-wide lexical scan is an expensive rescue path;
                    # reserve it for open thematic/relational/temporal and
                    # numeric retrieval so simple metadata/identifier requests
                    # do not pay a second full-index query.
                    and (
                        retrieval_intent(query) in {"thematic", "relational", "temporal"}
                        or is_table_route
                    )
                )
                current_title_query = ""
                title_document_ids = (
                    await repository.search_title_documents(
                        query, dataset_id=dataset_id, limit=4
                    )
                    if document_recall_enabled and hasattr(repository, "search_title_documents")
                    else []
                )
                # A question that names an old instrument but asks for the
                # current rule needs a second, year-neutral title recall. The
                # query is derived from the user's own words; this prevents a
                # historical year from starving retrieval of the governing
                # current statute.
                if (
                    re.search(r"\b(?:19|20)\d{2}\b", query)
                    and any(marker in query.casefold() for marker in ("hiện nay", "hiện hành"))
                ):
                    current_title_query = re.sub(r"\b(?:19|20)\d{2}\b", " ", query)
                    current_title_query = re.sub(
                        r"\b(?:hiện nay|hiện hành|nào|quy định)\b", " ", current_title_query,
                        flags=re.IGNORECASE,
                    ).replace("Thông tư", "Luật").replace("thông tư", "luật")
                    title_document_ids = list(
                        dict.fromkeys(
                            [
                                *title_document_ids,
                                *await repository.search_title_documents(
                                    current_title_query, dataset_id=dataset_id, limit=4
                                ),
                            ]
                        )
                    )[:8]
                # Passage retrieval is intentionally broad, but the decisive
                # clause can be short and rank below verbose background text.
                # Independently recall a small set of documents from the
                # canonical lexical index, then inspect their passages.  This
                # is a query-derived candidate stage (not an answer mapping):
                # title hits, lexical document hits and ANN hits still have to
                # produce a grounded passage and pass the shared reranker.
                document_recall_ids: list[str] = []
                # Keep the query-derived authority seed separate from the
                # broader lexical recall set.  It is used only as a bounded
                # source-diversity signal after canonical passage hydration;
                # the IDs themselves never cross the public boundary.
                authority_document_ids: list[str] = []
                if document_recall_enabled and hasattr(repository, "search_lexical_document_ids"):
                    try:
                        # Optional document-level rescue is bounded so a slow
                        # free-tier SQL connection cannot consume the request
                        # budget; primary lexical and dense recall remain valid.
                        document_recall_ids = await asyncio.wait_for(
                            repository.search_lexical_document_ids(
                                query,
                                dataset_id=dataset_id,
                                limit=settings.retrieval_candidate_k,
                            ),
                            timeout=min(4.0, settings.retrieval_timeout_seconds / 3),
                        )
                    except (TimeoutError, OSError, RuntimeError) as exc:
                        logger.warning("Optional document recall skipped (%s)", type(exc).__name__)
                if route_plan.risk == "high" and not exact_document_ids:
                    # High-risk questions often use colloquial wording that is
                    # absent from the current statute (for example emergency
                    # care without a referral). Seed a tiny set of verified
                    # current primary instruments, then let the
                    # passage matcher/reranker decide relevance. This is not
                    # a question-to-document answer mapping and is still
                    # bounded to the current release.
                    try:
                        current_authority_ids = await self._current_authority_ids(
                            repository,
                            query=query,
                            dataset_id=dataset_id,
                            limit=min(8, settings.retrieval_candidate_k),
                        )
                    except Exception:
                        current_authority_ids = []
                    authority_document_ids = list(current_authority_ids)
                    document_recall_ids = list(
                        dict.fromkeys([*current_authority_ids, *document_recall_ids])
                    )[: settings.retrieval_candidate_k]
                if current_title_query and hasattr(repository, "search_lexical_document_ids"):
                    try:
                        current_recall_ids = await asyncio.wait_for(
                            repository.search_lexical_document_ids(
                                current_title_query,
                                dataset_id=dataset_id,
                                limit=settings.retrieval_candidate_k,
                            ),
                            timeout=min(4.0, settings.retrieval_timeout_seconds / 3),
                        )
                    except (TimeoutError, OSError, RuntimeError) as exc:
                        logger.warning(
                            "Optional current-law recall skipped (%s)", type(exc).__name__
                        )
                        current_recall_ids = []
                    document_recall_ids = list(
                        dict.fromkeys(
                            [
                                *current_recall_ids,
                                *document_recall_ids,
                            ]
                        )
                    )[: settings.retrieval_candidate_k]
                document_semantic_candidate_ids = list(
                    dict.fromkeys([*title_document_ids, *document_recall_ids])
                )[: settings.retrieval_candidate_k]
                # The document-wide lexical scan is more expensive than a
                # Qdrant filter.  It needs only the strongest candidates;
                # the full recall set is still used by the dense re-query
                # below, then the shared reranker decides final evidence.
                document_candidate_ids = document_semantic_candidate_ids[:24]
                legal_labels = extract_legal_labels(query)
                page_results = _verified_evidence(
                    await repository.resolve_legal_units(
                        legal_labels,
                        dataset_id=dataset_id,
                        document_ids=exact_document_ids,
                    )
                ) if legal_labels and exact_document_ids else []
            _record_trace_event("postgres:release_recall", phase1_started, result_count=len(page_results))

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

            phase2_started = time.perf_counter()
            # Resolve a potentially stale env alias only after the phase-1 SQL
            # session has closed. The exact release point count prevents
            # accidentally selecting a second snapshot with similar vectors.
            try:
                await self._resolve_qdrant_release(
                    dataset_id=dataset_id,
                    expected_points=expected_points,
                    preferred_collection=qdrant_locator,
                )
            except Exception:
                logger.warning("Qdrant release resolution failed; lexical fallback remains available", exc_info=True)
            # Phase 2: independent lexical/provider work. The lexical task owns
            # its own short-lived DB session, so provider wait cannot pin it.
            # An explicit public document number is a hard retrieval boundary.
            # For thematic questions, reuse the query-derived document recall
            # set as a soft lexical scope as well: it keeps lexical ranking
            # focused on the governing instruments instead of returning the
            # first UUID-ordered chunks from the whole corpus.
            # For high-risk entitlement questions, the current-authority seed
            # is the safer lexical/dense boundary than the broad document
            # recall union.  The latter intentionally contains many
            # query-matching administrative documents and can swamp the
            # operative statute when the managed SQL query reaches the route
            # fallback.  The seed is query-derived from release metadata and
            # still must produce a matching canonical passage; it is not an
            # answer/document map.
            scoped_authority_ids = (
                authority_document_ids
                if route_plan.risk == "high" and authority_document_ids
                else document_recall_ids
            )
            search_document_ids = exact_document_ids or global_document_ids or scoped_authority_ids or None
            # The final context is at most a dozen passages. Fetching 60
            # candidates makes the subsequent hydrate/scope CTE dominate
            # latency on managed Postgres without improving the top-ranked
            # evidence. Keep a bounded 2x context head for the reranker.
            passage_candidate_limit = min(
                settings.retrieval_candidate_k,
                route_plan.max_candidates,
                max(settings.max_llm_evidence * 2, 24),
            )
            lexical_task = asyncio.create_task(
                lexical_search(
                    dataset_id=dataset_id,
                    document_ids=search_document_ids,
                    limit=passage_candidate_limit,
                )
            )

            async def lexical_budget_fallback() -> RetrievalBundle:
                """Return canonical lexical evidence when optional providers time out."""
                try:
                    # Lexical search is optional. Never let a saturated
                    # managed Postgres pool turn the route fallback into an
                    # unbounded wait beyond the advertised deadline.
                    lexical_results = await asyncio.wait_for(
                        lexical_task,
                        timeout=max(0.05, min(1.5, route_deadline - time.perf_counter())),
                    )
                except (TimeoutError, asyncio.CancelledError):
                    lexical_task.cancel()
                    await asyncio.gather(lexical_task, return_exceptions=True)
                    lexical_results = []
                except Exception:
                    lexical_results = []
                evidence = weighted_rrf(
                    {"lexical": lexical_results},
                    limit=settings.max_llm_evidence,
                    max_per_document=settings.max_chunks_per_document,
                )
                _record_trace_event(
                    "route:budget_fallback",
                    route_started,
                    route=route_plan.route,
                    budget_ms=route_plan.retrieval_budget_ms,
                    lexical_count=len(lexical_results),
                )
                return RetrievalBundle(
                    evidence=_verified_evidence(
                        exclude_unverified_legacy_subordinate_sources(
                            query,
                            filter_current_authority_candidates(query, evidence),
                        )
                    ),
                    relations=[],
                )

            # Numeric/table questions must use the structured fact/calculator
            # path. Until a fact row is available, keep the fallback lexical
            # and avoid paying for an embedding/ANN round trip that cannot
            # decide an exact amount safely.
            table_results: list[RetrievalResult] = []
            table_fact_task: asyncio.Task[list[RetrievalResult]] | None = None
            if is_table_route:
                table_started = time.perf_counter()

                async def _table_facts() -> list[RetrievalResult]:
                    try:
                        async with session_scope() as table_session:
                            result = await asyncio.wait_for(
                                GraphRepository(table_session).search_table_facts(
                                    query, dataset_id=dataset_id, limit=settings.max_llm_evidence
                                ),
                                # A missing/legacy reviewed-fact index must
                                # never consume the interactive route budget.
                                # The migration adds a partial index, while
                                # older releases fail open to dense retrieval.
                                timeout=min(1.0, max(0.1, route_deadline - time.perf_counter())),
                            )
                        _record_trace_event("postgres:table_facts", table_started, result_count=len(result))
                        return result
                    except Exception as exc:
                        # The projection is additive and may not exist during
                        # a rolling migration. Canonical retrieval remains a
                        # safe fallback and never fabricates a numeric fact.
                        _record_trace_event("postgres:table_facts", table_started, outcome=type(exc).__name__)
                        return []

                # Do not serialize structured lookup, lexical SQL and dense
                # recall. If no reviewed fact exists, the dense channel can
                # still recover the operative percentage clause.
                table_fact_task = asyncio.create_task(_table_facts())
            # Canonical lexical retrieval is the safe floor. Once the route
            # budget is consumed, do not start another remote provider call.
            if vector_override is None and time.perf_counter() >= route_deadline:
                metrics.inc("retrieval_route_budget_exhausted", route=route_plan.route)
                return await lexical_budget_fallback()
            async with trace_span(
                "embedding-query",
                as_type="embedding",
                input={"query_length": len(query)},
                metadata={"model": settings.embedding_model},
            ) as span:
                if vector_override is not None:
                    vector = vector_override
                else:
                    remaining = route_deadline - time.perf_counter()
                    if remaining <= 0:
                        return await lexical_budget_fallback()
                    try:
                        vector = await asyncio.wait_for(
                            self._embed_query(query), timeout=remaining
                        )
                    except TimeoutError:
                        metrics.inc("retrieval_route_budget_exhausted", route=route_plan.route)
                        return await lexical_budget_fallback()
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
                            query_text=query,
                            dataset_id=dataset_id,
                            document_ids=search_document_ids,
                            limit=passage_candidate_limit,
                            score_threshold=settings.semantic_similarity_threshold,
                        )
                    )
                    # The first semantic pass maximizes corpus-wide recall.
                    # Re-query the small document-recall set in parallel so a
                    # short operative clause can compete semantically even
                    # when it contains only one literal user term. This is a
                    # standard retrieve-then-rerank cascade and the document
                    # IDs remain private candidates, never citations.
                    document_semantic_task = (
                        asyncio.create_task(
                            self._search_vectors(
                                vector,
                                query_text=query,
                                dataset_id=dataset_id,
                                document_ids=document_semantic_candidate_ids,
                                limit=min(24, settings.retrieval_candidate_k),
                                score_threshold=settings.semantic_similarity_threshold,
                            )
                        )
                        if document_semantic_candidate_ids
                        else None
                    )
                    remaining = route_deadline - time.perf_counter()
                    if remaining <= 0:
                        semantic_task.cancel()
                        if document_semantic_task is not None:
                            document_semantic_task.cancel()
                        await asyncio.gather(semantic_task, return_exceptions=True)
                        if document_semantic_task is not None:
                            await asyncio.gather(document_semantic_task, return_exceptions=True)
                        return await lexical_budget_fallback()
                    try:
                        vector_result = await asyncio.wait_for(semantic_task, timeout=remaining)
                    except TimeoutError:
                        metrics.inc("retrieval_route_budget_exhausted", route=route_plan.route)
                        semantic_task.cancel()
                        if document_semantic_task is not None:
                            document_semantic_task.cancel()
                        await asyncio.gather(semantic_task, return_exceptions=True)
                        if document_semantic_task is not None:
                            await asyncio.gather(document_semantic_task, return_exceptions=True)
                        return await lexical_budget_fallback()
                    try:
                        lexical_result = await asyncio.wait_for(
                            lexical_task,
                            timeout=max(0.05, route_deadline - time.perf_counter()),
                        )
                    except TimeoutError:
                        lexical_task.cancel()
                        await asyncio.gather(lexical_task, return_exceptions=True)
                        lexical_result = []
                    # Lexical recall is an optional channel.  A slow/failed
                    # PostgreSQL full-text query must not discard a valid
                    # dense result (or turn the whole chat into a 503).
                    if isinstance(lexical_result, BaseException):
                        metrics.inc("retrieval_optional_failures", stage="lexical")
                        lexical_results = []
                    else:
                        lexical_results = lexical_result
                    if isinstance(vector_result, BaseException):
                        raise vector_result
                    vector_hits = vector_result
                    if document_semantic_task is not None:
                        document_vector_result = await document_semantic_task
                        document_vector_hits = (
                            []
                            if isinstance(document_vector_result, BaseException)
                            else document_vector_result
                        )
                        if isinstance(document_vector_result, BaseException):
                            metrics.inc("retrieval_optional_failures", stage="document_semantic")
                    else:
                        document_vector_hits = []
                else:
                    lexical_results = await asyncio.wait_for(
                        lexical_task,
                        timeout=max(0.05, route_deadline - time.perf_counter()),
                    )
                    vector_hits = list(vector_hits_override)
                    document_vector_hits = []
                if span is not None:
                    span.update(output={"result_count": len(vector_hits)})
            _record_trace_event(
                "retrieval:provider_fusion",
                phase2_started,
                lexical_count=len(lexical_results),
                semantic_count=len(vector_hits),
            )

            # Phase 3: bounded hydration/sibling expansion only.
            phase3_started = time.perf_counter()
            async with session_scope() as hydration_session:
                hydration_repository = GraphRepository(hydration_session)
                # Dense passage recall can identify the governing instrument
                # even when its title contains none of the user's wording.
                # Feed those IDs into the bounded operative scan when the
                # repository supports that optimized path. The capability
                # guard keeps lightweight test doubles and older deployments
                # on the original single lexical call.
                if hasattr(hydration_repository, "search_document_operatives"):
                    semantic_document_ids = [
                        item.document_id
                        for item in [*vector_hits, *document_vector_hits]
                        if getattr(item, "document_id", "")
                    ]
                    document_candidate_ids = list(
                        dict.fromkeys([*document_candidate_ids, *semantic_document_ids])
                    )[: max(24, min(48, settings.retrieval_candidate_k))]
                if hasattr(hydration_repository, "hydrate_chunks_with_scope"):
                    try:
                        hydrated, semantic_scope = await bounded_db(
                            hydration_repository.hydrate_chunks_with_scope(
                                [item.chunk_id for item in vector_hits],
                                dataset_id=dataset_id,
                                # Sibling enumeration is only meaningful when the user
                                # identified a document and legal unit. Expanding every
                                # thematic ANN hit used to inject unrelated a)/b) units
                                # with an artificial score of 1.0.
                                scope_limit=(
                                    # Scope expansion is a candidate pool, not final
                                    # context. A few older roots can otherwise use
                                    # all 12 slots before the current article's last
                                    # operative point (for example h)) is reached.
                                    max(24, settings.max_llm_evidence)
                                    if (intent == "legal_unit" and exact_document_ids)
                                    or requires_clause_expansion(query)
                                    else 0
                                ),
                            ),
                            "hydrate_scope",
                        )
                    except TimeoutError:
                        return await lexical_budget_fallback()
                else:
                    try:
                        hydrated = await bounded_db(
                            hydration_repository.hydrate_chunks(
                                [item.chunk_id for item in vector_hits], dataset_id=dataset_id
                            ),
                            "hydrate",
                        )
                    except TimeoutError:
                        return await lexical_budget_fallback()
                    fallback_focus = [
                        item for item in hydrated
                        if _is_enumerated_unit(item.section_title or item.content)
                    ]
                    try:
                        semantic_scope = await bounded_db(
                            hydration_repository.expand_sibling_legal_units(
                                [item.unit_id for item in fallback_focus if item.unit_id],
                                dataset_id=dataset_id,
                                limit=settings.max_llm_evidence,
                            ),
                            "hydrate_siblings",
                        )
                    except TimeoutError:
                        semantic_scope = []
                legal_reference_results: list[RetrievalResult] = []
                document_recall_semantic_results: list[RetrievalResult] = []
                if document_vector_hits:
                    try:
                        candidate_hydrated = await bounded_db(
                            hydration_repository.hydrate_chunks(
                                [item.chunk_id for item in document_vector_hits], dataset_id=dataset_id,
                            ),
                            "hydrate_document_semantic",
                        )
                    except TimeoutError:
                        candidate_hydrated = []
                    document_recall_semantic_results = rerank_legal_candidates(
                        query, _verify_hydrated_hits(candidate_hydrated, document_vector_hits)
                    )
                if requires_clause_expansion(query) and exact_document_ids:
                    # References such as “điểm b khoản 4 Điều 12” recur in
                    # many unrelated regulations.  Expand only the few
                    # strongest directly retrieved documents, then use the
                    # shared reference inside those documents; expanding
                    # every broad ANN hit would pull in military/local rules.
                    reference_seed_items = [*hydrated, *lexical_results, *semantic_scope]
                    query_terms = {
                        token.casefold()
                        for token in re.findall(r"[0-9A-Za-zÀ-ỹĐđ]+", query)
                        if len(token) > 2
                    }
                    reference_targets: list[tuple[str, str]] = []
                    for item in reference_seed_items:
                        title_scope = item.title.casefold()
                        # The cross-reference expansion is for an
                        # implementing instrument that explicitly guides a
                        # statute. Other thematic hits frequently reuse the
                        # same article numbers for finance, police/military
                        # or local schemes, where an expansion would be a
                        # false legal chain.
                        if "hướng dẫn" not in title_scope or "luật" not in title_scope:
                            continue
                        source_text = f"{item.title} {item.section_title} {item.content}"
                        source_terms = {
                            token.casefold()
                            for token in re.findall(r"[0-9A-Za-zÀ-ỹĐđ]+", source_text)
                            if len(token) > 2
                        }
                        # Do not let an incidental reference from a broad
                        # ANN hit seed another document's legal chain.
                        if len(query_terms & source_terms) < 2:
                            continue
                        reference_targets.extend(
                            (item.document_id, reference)
                            for reference in extract_internal_legal_references([source_text])
                        )
                    if reference_targets:
                        try:
                            legal_reference_results = await bounded_db(
                                hydration_repository.expand_internal_references(
                                    reference_targets,
                                    dataset_id=dataset_id,
                                    # A grouped reference can have several preceding
                                    # administrative clauses before its operative
                                    # percentage/duration clause. Keep this bounded
                                    # pool wide enough for the ranker to see it.
                                    limit=min(20, settings.retrieval_candidate_k),
                                ),
                                "expand_references",
                            )
                        except TimeoutError:
                            legal_reference_results = []
                try:
                    ranking_metadata = await bounded_db(
                        hydration_repository.document_ranking_metadata(
                            [
                                item.document_id
                                for item in [
                                    *hydrated, *lexical_results, *semantic_scope, *legal_reference_results, *page_results
                                    , *document_recall_semantic_results
                                ]
                            ] + document_candidate_ids,
                            dataset_id=dataset_id,
                        ),
                        "ranking_metadata",
                    )
                except TimeoutError:
                    ranking_metadata = {}
                _apply_document_ranking_metadata(
                    [
                        # Lexical candidates are already canonical passages;
                        # they still need the same document metadata hydration
                        # as dense hits so public citations never lose their
                        # document number on the lexical-only fallback path.
                        *hydrated, *lexical_results, *semantic_scope, *legal_reference_results,
                        *page_results, *document_recall_semantic_results,
                    ],
                    ranking_metadata,
                )
                document_recall_semantic_results = rerank_legal_candidates(
                    query, document_recall_semantic_results
                )
                semantic_results = rerank_legal_candidates(
                    query, _verify_hydrated_hits(hydrated, vector_hits)
                )
                # Once dense recall already contains a current-authority
                # passage with an explicit numeric rule, skip the expensive
                # document-wide LIKE rescue. It is only needed when the
                # semantic channel failed to surface a usable percentage.
                table_semantic_sufficient = (
                    is_table_route
                    and any(
                        item.document_id in document_candidate_ids
                        and re.search(r"\d+(?:[.,]\d+)?\s*%", item.content)
                        for item in [
                            *semantic_results,
                            *document_recall_semantic_results,
                            *lexical_results,
                        ]
                    )
                )
                lexical_results = rerank_legal_candidates(query, lexical_results)
                semantic_scope = rerank_legal_candidates(query, semantic_scope)
                semantic_scope = [
                    item
                    for item in semantic_scope
                    if scope_evidence_matches_query(
                        query,
                        item,
                        candidate_pool=[*semantic_results, *lexical_results, *semantic_scope],
                    )
                ]
                legal_reference_results = rerank_legal_candidates(query, legal_reference_results)
                if semantic_scope and requires_clause_expansion(query):
                    # A child point is generated from a high-ranked parent
                    # legal unit, so its standalone lexical score starts at
                    # zero. Carry bounded parent relevance forward; otherwise
                    # the exact a)/b)/c) answer is always outranked by the
                    # parent heading that merely introduces the list.
                    parent_scores: dict[str, float] = {}
                    for item in semantic_results:
                        parent_scores[item.document_id] = max(
                            parent_scores.get(item.document_id, 0.0), float(item.score)
                        )
                    for item in semantic_scope:
                        inherited = parent_scores.get(item.document_id, 0.0) * 0.65
                        item.score += inherited
                        item.rank_details = {**item.rank_details, "scope_parent_relevance": inherited}
                    semantic_scope.sort(key=lambda item: (-item.score, item.document_id, item.chunk_id))
                page_results = rerank_legal_candidates(query, page_results)
                document_anchor_results: list[RetrievalResult] = []
                document_recall_operatives: list[RetrievalResult] = []
                if (
                    document_candidate_ids
                    and (not table_semantic_sufficient or is_table_route)
                    and hasattr(hydration_repository, "search_lexical")
                ):
                    # The document-level index has already bounded the
                    # corpus. Reuse the GIN-backed canonical passage query
                    # here; it is materially cheaper than a LIKE scan over
                    # every chunk and still returns the exact operative text.
                    try:
                        lexical_document_rows = await bounded_db(
                            hydration_repository.search_lexical(
                                query,
                                dataset_id=dataset_id,
                                document_ids=document_candidate_ids,
                                # The query is already document-bounded. Fetch a
                                # wider lexical head, then retain one best passage
                                # per candidate document so a verbose source cannot
                                # crowd out a short operative clause.
                                limit=min(200, settings.retrieval_candidate_k * 4),
                            ),
                            "document_lexical",
                        )
                    except Exception:
                        await hydration_session.rollback()
                        metrics.inc("retrieval_optional_failures", stage="document_lexical")
                        lexical_document_rows = []
                    seen_document_ids: set[str] = set()
                    document_recall_operatives = []
                    for item in lexical_document_rows:
                        if item.document_id in seen_document_ids:
                            continue
                        seen_document_ids.add(item.document_id)
                        document_recall_operatives.append(item)
                        if len(document_recall_operatives) >= min(24, settings.retrieval_candidate_k):
                            break
                if (
                    document_candidate_ids
                    and (not table_semantic_sufficient or is_table_route)
                    and requires_clause_expansion(query)
                    and hasattr(hydration_repository, "search_document_operatives")
                ):
                    recall_order = list(dict.fromkeys([*document_recall_ids, *title_document_ids]))
                    authority_candidates = [
                        identifier
                        for identifier in recall_order
                        if str(ranking_metadata.get(identifier, {}).get("document_type", "")).casefold()
                        in {"luật", "nghị định", "văn bản hợp nhất"}
                    ]
                    # Term-overlap expansion is the expensive fallback. Keep
                    # it on the strongest authority-ranked documents first;
                    # broad recall IDs have already contributed ANN/lexical
                    # evidence and must not make the SQL scan exceed the
                    # request deadline.
                    operative_limit = 4 if is_table_route else (16 if (
                        "dịch vụ" in query.casefold()
                        and ("chi trả" in query.casefold() or "được hưởng" in query.casefold())
                    ) else 8)
                    # Authority/type metadata is a stronger generic signal
                    # than a title-only match.  Put it first so a small
                    # numeric-route budget cannot be consumed by unrelated
                    # title hits before the governing statute is inspected.
                    operative_document_ids = list(
                        dict.fromkeys([*authority_candidates, *recall_order, *title_document_ids])
                    )[:operative_limit]
                    try:
                        operative_rows = await bounded_db(
                            hydration_repository.search_document_operatives(
                                operative_document_ids,
                                dataset_id=dataset_id,
                                # Phrase-only matching keeps generic words such as
                                # “luật”, “chi”, or “căn cứ” from flooding the bounded
                                # result set before the distinctive operative clause.
                                terms=extract_query_phrases(query, limit=16),
                                limit=min(48, settings.retrieval_candidate_k),
                                # A decisive short clause may contain only one
                                # query-derived phrase (for example a three-token
                                # service exclusion). The candidate document was
                                # already selected independently, so one exact phrase
                                # is sufficient here; requiring two silently drops
                                # the operative passage.
                                minimum_matches=1,
                            ),
                            "operative_phrase",
                        )
                    except Exception:
                        # Document-bounded expansion is a recall accelerator,
                        # not a correctness dependency.  On a saturated
                        # managed pool, preserve lexical/dense evidence and
                        # record the degraded channel instead of failing chat.
                        await hydration_session.rollback()
                        metrics.inc("retrieval_optional_failures", stage="document_operatives_phrase")
                        operative_rows = []
                    known_chunks = {item.chunk_id for item in document_recall_operatives}
                    document_recall_operatives.extend(
                        item for item in operative_rows if item.chunk_id not in known_chunks
                    )
                    # Formal statutes may use a different collocation from
                    # the user's phrase (for example “cơ sở cấp chuyên sâu”
                    # instead of “bệnh viện tuyến tỉnh”). A second bounded
                    # term-overlap pass can recover those passages without a
                    # domain synonym table; it remains restricted to the
                    # documents already selected above.  Do not pay for this
                    # scan when the phrase pass already returned a contiguous
                    # query-derived anchor: on managed Postgres this second
                    # LIKE/unnest query is commonly the largest latency cost.
                    phrase_anchor_found = any(
                        phrase.casefold() in f"{item.section_title} {item.content}".casefold()
                        and any(
                            marker in str(
                                ranking_metadata.get(item.document_id, {}).get("document_type", "")
                            ).casefold()
                            for marker in ("luật", "nghị định", "văn bản hợp nhất")
                        )
                        for item in document_recall_operatives
                        for phrase in extract_query_phrases(query, limit=16)
                        if len(phrase.split()) >= 3
                    )
                    query_years = [
                        int(value) for value in re.findall(r"\b(?:19|20)\d{2}\b", query)
                    ]
                    historical_lookup = bool(query_years and max(query_years) < date.today().year - 1)
                    if not historical_lookup and not phrase_anchor_found:
                        try:
                            term_rows = await bounded_db(
                                hydration_repository.search_document_operatives(
                                    operative_document_ids,
                                    dataset_id=dataset_id,
                                    terms=extract_query_terms(query, limit=16),
                                    # Keep the document-bounded rescue small; the
                                    # first lexical/ANN channels already provide
                                    # broad recall and this pass exists only to
                                    # recover a short operative percentage clause.
                                    limit=min(48, settings.retrieval_candidate_k * 2),
                                    minimum_matches=2,
                                ),
                                "operative_terms",
                            )
                        except Exception:
                            await hydration_session.rollback()
                            metrics.inc("retrieval_optional_failures", stage="document_operatives_terms")
                            term_rows = []
                        known_chunks = {item.chunk_id for item in document_recall_operatives}
                        document_recall_operatives.extend(
                            item for item in term_rows if item.chunk_id not in known_chunks
                        )
                    operative_units = list(
                        dict.fromkeys(
                            item.unit_id
                            for item in document_recall_operatives
                            if item.unit_id and "page_index" in item.channels
                        )
                    )
                    if (
                        operative_units
                        and not is_table_route
                        and hasattr(hydration_repository, "expand_sibling_legal_units")
                    ):
                        try:
                            sibling_operatives = await bounded_db(
                                hydration_repository.expand_sibling_legal_units(
                                    operative_units[:12],
                                    dataset_id=dataset_id,
                                    limit=min(48, settings.retrieval_candidate_k * 2),
                                ),
                                "operative_siblings",
                            )
                        except TimeoutError:
                            sibling_operatives = []
                        known_chunks = {item.chunk_id for item in document_recall_operatives}
                        document_recall_operatives.extend(
                            item for item in sibling_operatives if item.chunk_id not in known_chunks
                        )
                if (
                    not document_recall_operatives
                    and document_candidate_ids
                    and (not table_semantic_sufficient or is_table_route)
                    and requires_clause_expansion(query)
                    and hasattr(hydration_repository, "search_document_operatives")
                ):
                    candidate_terms = [
                        *extract_query_phrases(query, limit=16),
                        *extract_query_terms(query, limit=16),
                    ]
                    try:
                        document_recall_operatives = await bounded_db(
                            hydration_repository.search_document_operatives(
                                document_candidate_ids,
                                dataset_id=dataset_id,
                                terms=candidate_terms,
                                limit=min(12, settings.max_llm_evidence),
                                minimum_matches=2,
                            ),
                            "operative_fallback",
                        )
                    except TimeoutError:
                        document_recall_operatives = []
                    _apply_document_ranking_metadata(document_recall_operatives, ranking_metadata)
                    document_recall_operatives = rerank_legal_candidates(query, document_recall_operatives)
                if intent == "relational" and not exact_document_ids and hasattr(
                    hydration_repository, "search_document_operatives"
                ):
                    primary_seed = rerank_legal_candidates(query, [*semantic_results, *lexical_results])
                    primary_document_ids = list(
                        dict.fromkeys(item.document_id for item in primary_seed if item.document_id)
                    )[:2]
                    query_terms = [
                        *extract_query_phrases(query, limit=12),
                        *extract_query_terms(query, limit=12),
                    ]
                    if primary_document_ids and query_terms:
                        try:
                            anchors = await bounded_db(
                                hydration_repository.search_document_operatives(
                                    primary_document_ids,
                                    dataset_id=dataset_id,
                                    terms=query_terms,
                                    limit=min(8, settings.max_llm_evidence),
                                    minimum_matches=2,
                                ),
                                "relational_anchors",
                            )
                        except TimeoutError:
                            anchors = []
                        _apply_document_ranking_metadata(anchors, ranking_metadata)
                        anchors = rerank_legal_candidates(query, anchors)
                        reference_targets = [
                            (item.document_id, reference)
                            for item in anchors
                            for reference in extract_internal_legal_references(
                                [f"{item.section_title} {item.content}"]
                            )
                        ]
                        if reference_targets:
                            try:
                                linked = await bounded_db(
                                    hydration_repository.expand_internal_references(
                                        reference_targets,
                                        dataset_id=dataset_id,
                                        limit=min(20, settings.retrieval_candidate_k),
                                    ),
                                    "relational_references",
                                )
                            except TimeoutError:
                                linked = []
                            _apply_document_ranking_metadata(linked, ranking_metadata)
                            linked = rerank_legal_candidates(query, linked)
                            anchor_score = max((float(item.score) for item in anchors), default=0.0)
                            for item in linked:
                                item.score += anchor_score * 0.75
                                item.rank_details = {
                                    **item.rank_details,
                                    "anchor_reference_relevance": anchor_score * 0.75,
                                }
                            document_anchor_results = sorted(
                                [*anchors, *linked],
                                key=lambda item: (-item.score, item.document_id, item.chunk_id),
                            )
                # The fast GIN-backed branch and the bounded operative
                # fallback both return raw repository rows. Attach the same
                # canonical metadata before fusion; otherwise a correct law
                # can lose its public document number and authority score.
                _apply_document_ranking_metadata(document_recall_operatives, ranking_metadata)
                document_recall_operatives = rerank_legal_candidates(
                    query, document_recall_operatives
                )
                # A current primary instrument can be semantically relevant
                # yet lose the final RRF slots to verbose administrative
                # passages. Preserve at most one canonical passage per
                # query-derived authority candidate as a diversity channel.
                # This is metadata/relevance driven (never a document-number
                # map) and still requires source span + canonical hydration.
                authority_anchor_results: list[RetrievalResult] = []
                authority_ids = set(authority_document_ids)
                authority_pool = [
                    *document_recall_operatives,
                    *document_recall_semantic_results,
                    *lexical_results,
                    *semantic_results,
                ]
                seen_authority_documents: set[str] = set()
                for item in sorted(
                    authority_pool,
                    key=lambda value: (
                        -float(value.rank_details.get("semantic_rerank_score", value.score)),
                        value.document_id,
                        value.chunk_id,
                    ),
                ):
                    if not item.document_id or item.document_id in seen_authority_documents:
                        continue
                    metadata = ranking_metadata.get(item.document_id, {})
                    authority_type = str(
                        metadata.get("document_type") or item.document_type or ""
                    ).casefold()
                    is_primary_type = any(
                        marker in authority_type
                        for marker in ("luật", "nghị định", "văn bản hợp nhất")
                    )
                    query_coverage = float(item.rank_details.get("query_token_coverage", 0.0))
                    if item.document_id not in authority_ids and (
                        not is_primary_type or query_coverage < 0.12
                    ):
                        continue
                    if item.source_start is None or item.source_end is None:
                        continue
                    seen_authority_documents.add(item.document_id)
                    authority_anchor_results.append(item)
                    if len(authority_anchor_results) >= min(8, settings.max_llm_evidence):
                        break
                # Prefer a contiguous three-token phrase when the query has
                # one. Two-token phrases are a fallback only for short
                # questions; otherwise generic pairs such as “quy định hiện
                # hành” can select a historical preamble instead of the
                # operative clause containing the user's actual subject.
                all_anchor_phrases = extract_query_phrases(query, limit=16)
                three_token_anchors = [
                    phrase for phrase in all_anchor_phrases if len(phrase.split()) >= 3
                ]
                anchor_phrase_candidates = three_token_anchors or [
                    phrase for phrase in all_anchor_phrases if len(phrase.split()) >= 2
                ]
                # A legal unit often shortens the user's wording (for
                # example, “cấp cứu” instead of “điều trị nội trú cấp cứu
                # không có giấy chuyển tuyến”).  Recover such units with an
                # informative single token selected from this candidate set,
                # rather than a domain-specific synonym list.  Generic words
                # are excluded dynamically when they occur in most rows.
                candidate_texts = [
                    f"{item.section_title} {item.content}".casefold()
                    for item in document_recall_operatives
                ]
                query_term_anchors = [
                    term
                    for term in extract_query_terms(query, limit=16)
                    if sum(term in text for text in candidate_texts)
                    <= max(1, len(candidate_texts) // 2)
                ]
                document_exact_anchors = [
                    item
                    for item in document_recall_operatives
                    if (
                        any(
                            phrase.casefold() in f"{item.section_title} {item.content}".casefold()
                            for phrase in anchor_phrase_candidates
                        )
                        or any(
                            term in f"{item.section_title} {item.content}".casefold()
                            for term in query_term_anchors
                        )
                        or (
                            bool(re.search(r"\d|%", item.content.casefold()))
                            and sum(
                                term in f"{item.section_title} {item.content}".casefold()
                                for term in extract_query_terms(query, limit=16)
                            ) >= 2
                        )
                    )
                ]
                if (
                    document_exact_anchors
                    and requires_clause_expansion(query)
                    and not requires_evidence_verification(query)
                ):
                    anchor_phrases = anchor_phrase_candidates
                    phrase_frequency = {
                        phrase: sum(
                            phrase.casefold() in f"{candidate.section_title} {candidate.content}".casefold()
                            for candidate in document_recall_operatives
                        )
                        for phrase in anchor_phrases
                    }
                    document_exact_anchors.sort(
                        key=lambda item: (
                            -max(
                                (1.0 / phrase_frequency[phrase]
                                 for phrase in anchor_phrases
                                 if phrase.casefold() in f"{item.section_title} {item.content}".casefold()),
                                default=0.0,
                            ),
                            len(item.content),
                        )
                    )
                    # A canonical passage containing a multi-token phrase
                    # from the question is already a grounded answer seed.
                    # Return it directly so generic semantic distractors
                    # cannot evict the operative clause during RRF.
                    return RetrievalBundle(
                        evidence=_verified_evidence(document_exact_anchors[: settings.max_llm_evidence]),
                        relations=[],
                    )
                document_operatives: list[RetrievalResult] = []
                # A document-wide term scan is safe only for an explicit
                # document lookup. Thematic queries must first establish a
                # legal chain through normal retrieval/cross-reference logic;
                # otherwise generic words such as “hỗ trợ” pull unrelated
                # beneficiary groups from the same decree.
                if (
                    requires_clause_expansion(query)
                    and exact_document_ids
                    and hasattr(
                    hydration_repository, "search_document_operatives"
                    )
                ):
                    primary_seed = rerank_legal_candidates(
                        query, [*semantic_results, *lexical_results]
                    )
                    primary_document_ids = list(
                        dict.fromkeys(
                            item.document_id
                            for item in primary_seed
                            if item.document_id
                        )
                    )[:2]
                    query_phrases = extract_query_phrases(query)
                    if primary_document_ids and query_phrases:
                        try:
                            document_operatives = await bounded_db(
                                hydration_repository.search_document_operatives(
                                    primary_document_ids,
                                    dataset_id=dataset_id,
                                    terms=query_phrases,
                                    limit=min(12, settings.max_llm_evidence),
                                ),
                                "exact_operatives",
                            )
                        except TimeoutError:
                            document_operatives = []
                        _apply_document_ranking_metadata(document_operatives, ranking_metadata)
                        document_operatives = rerank_legal_candidates(query, document_operatives)
                        parent_scores = {
                            item.document_id: float(item.score)
                            for item in primary_seed
                            if item.document_id
                        }
                        for item in document_operatives:
                            inherited = parent_scores.get(item.document_id, 0.0) * 0.5
                            item.score += inherited
                            item.rank_details = {
                                **item.rank_details,
                                "document_parent_relevance": inherited,
                            }
                        document_operatives.sort(key=lambda item: (-item.score, item.document_id, item.chunk_id))

            _record_trace_event(
                "postgres:hydrate_rank",
                phase3_started,
                hydrated_count=len(hydrated),
                operative_count=len(document_recall_operatives),
            )
            channels: dict[str, Sequence[RetrievalResult]] = {
                "lexical": lexical_results,
                "semantic": semantic_results,
            }
            if table_fact_task is not None:
                try:
                    table_results = await asyncio.wait_for(
                        table_fact_task,
                        timeout=max(0.05, route_deadline - time.perf_counter()),
                    )
                except (TimeoutError, asyncio.CancelledError):
                    table_fact_task.cancel()
                    await asyncio.gather(table_fact_task, return_exceptions=True)
                    table_results = []
                if table_results:
                    channels["table_fact"] = table_results
            if page_results:
                channels["page_index"] = page_results
            if semantic_scope and intent == "legal_unit" and exact_document_ids:
                return RetrievalBundle(evidence=_verified_evidence(semantic_scope), relations=[])
            if semantic_scope and requires_clause_expansion(query):
                channels["semantic_scope"] = semantic_scope
            if legal_reference_results:
                channels["legal_reference"] = legal_reference_results
            if document_anchor_results:
                channels["document_anchor"] = document_anchor_results
            if authority_anchor_results:
                channels["authority_anchor"] = authority_anchor_results
            if document_recall_operatives:
                channels["document_recall_operatives"] = document_recall_operatives
            if document_recall_semantic_results:
                channels["document_recall_semantic"] = document_recall_semantic_results
            if document_operatives:
                channels["document_operatives"] = document_operatives

            graph_results: list = []
            typed_graph_results: list = []
            typed_fact_evidence: list[RetrievalResult] = []
            seed_ids = list(dict.fromkeys(item.document_id for item in weighted_rrf(channels, limit=6)))
            if settings.feature_graph_enabled and intent == "relational":
                typed_started = time.perf_counter()
                fact_subjects: list[str] = []
                try:
                    # Subject lookup is a cheap, release-scoped SQL seed. The
                    # typed graph is consulted only after this step, never for
                    # every ordinary topical request.
                    async with session_scope() as fact_session:
                        fact_repository = GraphRepository(fact_session)
                        fact_subjects = await fact_repository.search_legal_fact_subjects(
                            extract_query_terms(query), dataset_id=dataset_id, limit=8
                        )
                    if fact_subjects:
                        typed_graph = self._get_graph_store()
                        typed_walk = getattr(
                            typed_graph, "bounded_typed_ppr", typed_graph.expand_typed_facts
                        )
                        # Graph is an optional recall signal.  Keep its
                        # remote hop inside the same route deadline so an
                        # Aura/DNS stall cannot turn a relational request into
                        # a full-request timeout.
                        typed_timeout = max(0.05, route_deadline - time.perf_counter())
                        typed_relations = await asyncio.wait_for(
                            self._provider_call(
                                "neo4j_typed_facts",
                                self._neo4j_breaker,
                                lambda: typed_walk(
                                    fact_subjects,
                                    dataset_id=dataset_id,
                                    limit=settings.graph_evidence_limit,
                                ),
                            ),
                            timeout=typed_timeout,
                        )
                        typed_graph_results.extend(typed_relations)
                        graph_results.extend(typed_relations)
                        typed_unit_ids = [
                            relation.target_id for relation in typed_relations if relation.target_id
                        ]
                        if typed_unit_ids:
                            async with session_scope() as fact_hydration_session:
                                typed_fact_evidence = await GraphRepository(
                                    fact_hydration_session
                                ).hydrate_units_by_ids(
                                    typed_unit_ids,
                                    dataset_id=dataset_id,
                                    limit=settings.graph_evidence_limit,
                                )
                    _record_trace_event(
                        "neo4j:typed_facts",
                        typed_started,
                        subject_count=len(fact_subjects),
                        relation_count=len(graph_results),
                    )
                except Exception as exc:
                    # The typed-fact migration/projection is additive. A
                    # missing projection or Neo4j outage must preserve the
                    # canonical lexical+dense route.
                    metrics.inc("retrieval_optional_failures", stage="typed_facts")
                    _record_trace_event(
                        "neo4j:typed_facts", typed_started, outcome=type(exc).__name__
                    )
            # Graph traversal is valuable for an explicit reference chain or
            # a named instrument's temporal history.  For an ordinary
            # "hiện hành" question it adds a remote hop and a second database
            # hydration without improving passage recall; the canonical
            # lexical+dense path already carries currentness metadata.
            if (
                settings.feature_graph_enabled
                and (intent == "relational" or (intent == "temporal" and exact_document_ids))
                and seed_ids
            ):
                try:
                    async with trace_span(
                        "neo4j-expand", as_type="retriever", metadata={"dataset_id": dataset_id}
                    ) as span:
                        graph_timeout = max(0.05, route_deadline - time.perf_counter())
                        document_graph_results = await asyncio.wait_for(
                            self._provider_call(
                                "neo4j",
                                self._neo4j_breaker,
                                lambda: self._get_graph_store().expand(
                                    seed_ids,
                                    dataset_id=dataset_id,
                                    hops=min(settings.graph_hops, 2 if intent == "temporal" else 1),
                                    limit=settings.graph_neighbor_limit,
                                ),
                            ),
                            timeout=graph_timeout,
                        )
                        if span is not None:
                            span.update(output={"relation_count": len(document_graph_results)})
                    related_ids = list(
                        dict.fromkeys(
                            identifier
                            for relation in document_graph_results
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
                            query_text=query,
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
                            graph_evidence = _merge_evidence(graph_lexical, graph_semantic)
                            graph_metadata = await graph_repository.document_ranking_metadata(
                                [item.document_id for item in graph_evidence],
                                dataset_id=dataset_id,
                            )
                            _apply_document_ranking_metadata(graph_evidence, graph_metadata)
                        graph_evidence = rerank_legal_candidates(query, graph_evidence)
                        # A graph edge is navigation only.  Keep its relation
                        # in the model context only when at least one endpoint
                        # has been re-retrieved and provenance-verified from
                        # the canonical release.  This prevents a stale or
                        # mis-resolved edge from becoming an unsupported legal
                        # claim while preserving GraphRAG recall when the
                        # target passage is real.
                        hydrated_graph_ids = {
                            item.document_id
                            for item in graph_evidence
                            if (
                                item.document_id
                                and item.dataset_id == dataset_id
                                and item.text_sha256
                                and hashlib.sha256(item.content.encode("utf-8")).hexdigest()
                                == item.text_sha256
                            )
                        }
                        source_backed_relations = [
                            relation
                            for relation in document_graph_results
                            if relation.source_id in hydrated_graph_ids
                            or relation.target_id in hydrated_graph_ids
                        ]
                        graph_results.extend(
                            filter_relations_by_query(query, source_backed_relations)
                        )
                        if graph_evidence:
                            channels["legal_graph"] = graph_evidence
                except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
                    # Graph expansion improves recall but is never the sole
                    # evidence source. A DNS/Aura outage must degrade to the
                    # independently verified lexical+dense path, not turn a
                    # valid legal question into a 503/no-answer response.
                    graph_results = list(typed_graph_results)
                    metrics.inc("retrieval_optional_dependency_total", dependency="neo4j", outcome="fallback")
                    logger.warning(
                        "Neo4j expansion unavailable (%s); using lexical+dense retrieval",
                        type(exc).__name__,
                    )

            if typed_fact_evidence:
                channels["typed_fact"] = typed_fact_evidence

            fused_evidence = weighted_rrf(
                channels,
                limit=settings.max_llm_evidence,
                # Operational legal questions often require the beneficiary,
                # the governing group and the operative percentage/duration
                # from one statute. Preserve a slightly wider same-document
                # chain before the LLM audits it; thematic questions retain
                # diversity.
                max_per_document=(
                    max(settings.max_chunks_per_document, 6)
                    if requires_clause_expansion(query)
                    else settings.max_chunks_per_document
                ),
            )
            # Preserve the strongest document-bounded exact passage until the
            # final reranker sees it. RRF's small context cut can otherwise
            # evict a decisive clause that appears only in this channel.
            fused_by_chunk = {item.chunk_id: item for item in fused_evidence}
            query_phrases = extract_query_phrases(query, limit=16)
            anchor_phrases = [
                phrase for phrase in query_phrases if len(phrase.split()) >= 2
            ]
            query_terms_for_anchor = extract_query_terms(query, limit=16)
            operative_anchors = []
            for item in document_recall_operatives:
                source_text = f"{item.section_title} {item.content}".casefold()
                phrase_hits = sum(phrase.casefold() in source_text for phrase in anchor_phrases)
                term_hits = sum(term in source_text for term in query_terms_for_anchor)
                has_structured_value = bool(re.search(r"\d|%", source_text))
                if phrase_hits or (has_structured_value and term_hits >= 2):
                    item.rank_details = {
                        **item.rank_details,
                        "query_anchor_phrase_hits": float(phrase_hits),
                        "query_anchor_term_hits": float(term_hits),
                    }
                    operative_anchors.append(item)
            operative_anchors.sort(
                key=lambda item: (
                    float(item.rank_details.get("query_anchor_phrase_hits", 0.0)),
                    float(item.rank_details.get("query_anchor_term_hits", 0.0)),
                    float(item.score),
                ),
                reverse=True,
            )
            for item in operative_anchors[:2]:
                # Preserve the exact phrase signal through the final
                # source-aware reranker; this is still query-derived and
                # comes from a canonical passage, never from a mapping.
                item.score += 10.0
            for item in [*operative_anchors[:2], *document_recall_operatives[:1]]:
                fused_by_chunk.setdefault(item.chunk_id, item)
            fused_evidence = list(fused_by_chunk.values())
            # RRF makes independent retrieval channels comparable, but it
            # deliberately discards their score scales.  Apply the
            # source-derived legal ranking once more after fusion so an exact,
            # distinctive operative phrase is not evicted by several generic
            # dense/BM25 matches that merely co-occur across channels.
            if settings.feature_reranker_enabled:
                fused_evidence = rerank_legal_candidates(query, fused_evidence)
            fused_evidence = filter_current_authority_candidates(query, fused_evidence)
            fused_evidence = exclude_unverified_legacy_subordinate_sources(query, fused_evidence)
            # Scope expansion is useful only when the selected passage still
            # carries a query-derived distinctive phrase/term.  This prevents
            # a long historical preamble or an unrelated payment table from
            # surviving merely because it shares generic legal vocabulary.
            if requires_evidence_verification(query) and len(fused_evidence) > 1:
                scoped = [
                    item
                    for item in fused_evidence
                    if scope_evidence_matches_query(
                        query, item, candidate_pool=fused_evidence
                    )
                ]
                if scoped:
                    fused_evidence = scoped
            if operative_anchors:
                anchor_ids = {item.chunk_id for item in operative_anchors[:2]}
                fused_evidence = operative_anchors[:2] + [
                    item for item in fused_evidence if item.chunk_id not in anchor_ids
                ]
            if is_table_route:
                # Numeric routes must keep an explicit source value in the
                # final context. RRF can otherwise prefer a heading that
                # explains the rule while evicting the neighbouring 50%/100%
                # clause needed by synthesis. Select only source passages
                # that contain a value and at least two query-derived terms.
                anchor_terms = extract_query_terms(query, limit=16)
                numeric_candidates = [
                    item
                    for item in fused_evidence
                    if re.search(r"\d+(?:[.,]\d+)?\s*%", item.content)
                    and sum(term in item.content.casefold() for term in anchor_terms) >= 2
                ]
                numeric_candidates.sort(key=lambda item: (-float(item.score), item.document_id, item.chunk_id))
                numeric_ids = {item.chunk_id for item in numeric_candidates[:3]}
                if numeric_ids:
                    promoted = [item for item in numeric_candidates if item.chunk_id in numeric_ids]
                    fused_evidence = promoted + [
                        item for item in fused_evidence if item.chunk_id not in numeric_ids
                    ]
            # Keep a small, source-derived rescue set after the final fusion
            # and authority filters.  RRF can otherwise evict the decisive
            # clause when a broad lexical hit appears in several channels.
            # These are not answer mappings: anchors are selected solely by
            # query phrase/term overlap and must still pass canonical hash
            # verification below.
            if document_recall_operatives and requires_clause_expansion(query):
                query_phrases = extract_query_phrases(query, limit=24)
                query_terms = extract_query_terms(query, limit=24)
                # Retain contiguous bigrams with function words as a second
                # signal. The normalized retrieval phrases intentionally drop
                # stopwords, but legal operative wording often hinges on a
                # phrase such as “được hưởng” or “không có”.
                raw_tokens = [
                    token.casefold()
                    for token in re.findall(r"[0-9A-Za-zÀ-ỹĐđ]+", query)
                ]
                raw_bigrams = [
                    " ".join(raw_tokens[index : index + 2])
                    for index in range(len(raw_tokens) - 1)
                ]
                value_intent = bool(
                    re.search(
                        r"%|bao\s+nhiêu|phần\s+trăm|tỷ\s+lệ|mức\s+hưởng|được\s+hưởng|mức\s+đóng|mức\s+hỗ\s+trợ|số\s+tiền|tính",
                        query.casefold(),
                    )
                )
                source_anchors: list[tuple[float, int, int, RetrievalResult]] = []
                anchor_source_items = [
                    *document_recall_operatives,
                    *document_recall_semantic_results,
                ]
                for item in anchor_source_items:
                    # Rank the operative text itself, not a parent heading
                    # copied into a neighbouring chunk. Parent-prefixed rows
                    # make unrelated clauses (for example transport) appear
                    # to match every query term and can hide the decisive
                    # entitlement sentence.
                    text_value = item.content.casefold()
                    phrase_hits = sum(phrase.casefold() in text_value for phrase in query_phrases)
                    phrase_hits += 2 * sum(
                        phrase in text_value for phrase in raw_bigrams
                    )
                    # Parent-prefixed rows can contain a whole article and
                    # therefore match many query terms by length alone. Use
                    # phrase density so a short operative sentence outranks a
                    # verbose heading that merely repeats the same words.
                    token_count = max(1, len(re.findall(r"[0-9A-Za-zÀ-ỹĐđ]+", text_value)))
                    phrase_score = phrase_hits * 100.0 / token_count
                    term_hits = sum(term in text_value for term in query_terms)
                    has_value = bool(
                        re.search(
                            r"\d+(?:[.,]\d+)?\s*%|\b\d+\s*(?:lần|ngày|năm|tháng)\b",
                            text_value,
                        )
                    )
                    if not phrase_hits and not (has_value and term_hits >= 2):
                        continue
                    has_percent = bool(re.search(r"\d+(?:[.,]\d+)?\s*%", text_value))
                    value_priority = (
                        2
                        if has_percent or (value_intent and "mức hưởng" in text_value)
                        else int(has_value)
                    )
                    source_anchors.append((phrase_score, term_hits, value_priority, item))
                source_anchors.sort(
                    key=lambda row: (
                        # A query that asks for a benefit/percentage must see
                        # a source-backed entitlement value before a generic
                        # transport or procedural sentence. This is a dynamic
                        # signal from the query and passage, not an answer map.
                        -row[2] if (is_table_route or value_intent) else -row[0],
                        -row[0] if (is_table_route or value_intent) else -row[1],
                        -row[1] if (is_table_route or value_intent) else -row[2],
                        -float(row[3].score),
                        row[3].document_id, row[3].chunk_id,
                    )
                )
                # Keep a small second set of high-value clauses whose text
                # contains an exact user-word bigram. This prevents several
                # generic percentage rows from evicting a decisive exception
                # sentence (for example an emergency entitlement) while
                # remaining entirely query/source derived.
                exact_value_anchors = [
                    row
                    for row in source_anchors
                    if row[2] >= 2
                    and any(phrase in row[3].content.casefold() for phrase in raw_bigrams)
                ]
                exact_phrase_anchors: list[tuple[float, int, int, RetrievalResult]] = []
                for phrase in raw_bigrams:
                    matching = [
                        row for row in source_anchors if phrase in row[3].content.casefold()
                    ]
                    if matching:
                        exact_phrase_anchors.append(matching[0])
                current_anchor_rows = [
                    row
                    for row in source_anchors
                    if any(
                        int(value) >= date.today().year - 2
                        for value in re.findall(r"\b(?:19|20)\d{2}\b", row[3].effective_from)
                    )
                ]
                current_anchor_rows.sort(
                    key=lambda row: (-row[2], -row[0], -row[1], row[3].document_id, row[3].chunk_id)
                )
                selected_anchor_rows: list[tuple[float, int, int, RetrievalResult]] = []
                selected_anchor_ids: set[str] = set()
                for row in [
                    *source_anchors[:3],
                    *current_anchor_rows[:3],
                    *exact_value_anchors[:3],
                    # Keep enough distinct query bigrams to cover a
                    # short exception clause as well as the broader rule.
                    *exact_phrase_anchors[:6],
                ]:
                    if row[3].chunk_id in selected_anchor_ids:
                        continue
                    selected_anchor_ids.add(row[3].chunk_id)
                    selected_anchor_rows.append(row)
                verified_anchor_items = _verified_evidence(
                    [row[3] for row in selected_anchor_rows]
                )
                anchor_ids = {item.chunk_id for item in verified_anchor_items}
                if anchor_ids:
                    fused_evidence = verified_anchor_items + [
                        item for item in fused_evidence if item.chunk_id not in anchor_ids
                    ]
                    # Anchor rescue may add several distinct query phrases;
                    # retain the normal context/evidence ceiling after
                    # promotion so latency and prompt size stay bounded.
                    fused_evidence = fused_evidence[: settings.max_llm_evidence]
                # For present-day numeric/entitlement questions, prefer a
                # recent primary passage that actually contains the queried
                # value and at least two query-derived terms.  This is a
                # generic currentness/value tie-breaker: it does not name a
                # statute or answer, and it prevents a historical percentage
                # from remaining first merely because its ANN score was high.
                if value_intent and not extract_document_numbers(query):
                    current_year = date.today().year
                    query_value_terms = extract_query_terms(query, limit=24)
                    current_value_candidates: list[tuple[int, int, float, RetrievalResult]] = []
                    for item in fused_evidence:
                        years = [
                            int(value)
                            for value in re.findall(
                                r"\b(?:19|20)\d{2}\b",
                                " ".join((item.issued_date, item.effective_from, item.document_number, item.title)),
                            )
                        ]
                        if max(years, default=0) < current_year - 2:
                            continue
                        text_value = f"{item.section_title} {item.content}".casefold()
                        if not re.search(r"\d+(?:[.,]\d+)?\s*%|\b\d+\s*(?:lần|ngày|năm|tháng)\b", text_value):
                            continue
                        term_hits = sum(term in text_value for term in query_value_terms)
                        if term_hits < 2:
                            continue
                        phrase_hits = sum(
                            phrase.casefold() in text_value
                            for phrase in extract_query_phrases(query, limit=16)
                        )
                        current_value_candidates.append((phrase_hits, term_hits, float(item.score), item))
                    current_value_candidates.sort(
                        key=lambda row: (-row[0], -row[1], -row[2], row[3].document_id, row[3].chunk_id)
                    )
                    if current_value_candidates:
                        preferred_ids = {
                            row[3].chunk_id for row in current_value_candidates[:2]
                        }
                        preferred = [
                            row[3] for row in current_value_candidates[:2]
                        ]
                        fused_evidence = preferred + [
                            item for item in fused_evidence if item.chunk_id not in preferred_ids
                        ]
            _record_trace_event(
                "retrieval:rerank_select",
                phase3_started,
                selected_count=len(fused_evidence),
                channel_count=len(channels),
                operative_value_count=sum(
                    bool(re.search(r"\d+(?:[.,]\d+)?\s*%", item.content))
                    for item in document_recall_operatives
                ),
                operative_entitlement_count=sum(
                    "cấp cứu" in item.content.casefold()
                    and "mức hưởng" in item.content.casefold()
                    for item in document_recall_operatives
                ),
                selected_entitlement_count=sum(
                    "cấp cứu" in item.content.casefold()
                    and "mức hưởng" in item.content.casefold()
                    for item in fused_evidence
                ),
            )
            return RetrievalBundle(
                evidence=_verified_evidence(fused_evidence),
                relations=graph_results,
            )
        except GraphRagUnavailableError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise GraphRagUnavailableError("GraphRAG dependencies are unavailable") from exc
        except Exception as exc:
            raise GraphRagUnavailableError("GraphRAG retrieval failed") from exc

    async def generate(
        self, query: str, context: str, *, timeout_seconds: float | None = None
    ) -> str:
        started = time.perf_counter()
        settings = get_settings()
        generation_timeout = (
            max(0.25, float(timeout_seconds))
            if timeout_seconds is not None
            else settings.llm_timeout_seconds
        )
        system_prompt, prompt_version = resolve_prompt(SYSTEM_PROMPT)
        generation_trace: dict[str, Any] = {
            "stage": "generation",
            "model": settings.model_name,
            "model_version": settings.model_name,
            "prompt_version": prompt_version,
            "release_id": "",
            "outcome": "pending",
        }
        _generation_context.set(generation_trace)
        normalized_query = " ".join(query.casefold().split())
        current_release = self._active_release[0] if self._active_release else ""
        generation_trace["release_id"] = current_release
        answer_namespace = (
            current_release,
            settings.model_name,
            prompt_version,
            settings.llm_temperature,
            settings.llm_max_output_tokens,
            _RETRIEVAL_POLICY_VERSION,
        )
        context_digest = hashlib.sha256(context.encode("utf-8")).hexdigest()
        answer_key = (answer_namespace, f"{normalized_query}\n{context_digest}")
        cache_allowed = _answer_cache_allowed(query)
        cached = self._answer_cache.get(answer_key) if current_release and context and cache_allowed else None
        if cached and time.monotonic() - cached[1] < 60:
            generation_trace["outcome"] = "cache"
            generation_trace["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
            metrics.inc("generation_requests_total", outcome="cache")
            metrics.observe("generation_duration_seconds", time.perf_counter() - started, outcome="cache")
            return cached[0]
        answer_instruction = _answer_format_instruction(query)
        try:
            llm = get_llm()
            structured = hasattr(llm, "with_structured_output")
            generation_trace["structured_output"] = structured
            generation_llm = (
                # Keep the raw AIMessage alongside the parsed Pydantic object.
                # Responses API usage metadata is attached to that raw message;
                # without it a real provider run appears free and cannot be
                # reconciled with the billing dashboard.
                llm.with_structured_output(
                    GroundedAnswer,
                    method="json_schema",
                    include_raw=True,
                )
                if structured
                else llm
            )
            result = await asyncio.wait_for(
                generation_llm.ainvoke(
                    [
                        SystemMessage(content=system_prompt),
                        HumanMessage(
                            content=(
                                f"Câu hỏi người dùng:\n{query}\n\n"
                                f"Nguồn pháp lý được phép sử dụng:\n{context}\n\n"
                                f"Định dạng đầu ra bắt buộc:\n{answer_instruction}"
                            )
                        ),
                    ],
                    config=llm_invoke_config() or None,
                ),
                timeout=generation_timeout,
            )
        except TimeoutError:
            generation_trace.update(
                outcome="timeout",
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            metrics.inc("generation_requests_total", outcome="timeout")
            metrics.observe("generation_duration_seconds", time.perf_counter() - started, outcome="timeout")
            return NO_EVIDENCE_RESPONSE
        except Exception as exc:
            generation_trace.update(
                outcome=type(exc).__name__,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            metrics.inc("generation_requests_total", outcome="error")
            metrics.observe("generation_duration_seconds", time.perf_counter() - started, outcome="error")
            raise ChatProviderError("Chat provider failed") from exc
        # ``include_raw=True`` returns {raw, parsed, parsing_error}; older
        # wrappers/mocks may still return the parsed object directly. Render
        # only the parsed value, while reading usage from the raw message.
        raw_result = result.get("raw") if isinstance(result, dict) and "parsed" in result else result
        parsed_result = result.get("parsed") if isinstance(result, dict) and "parsed" in result else result
        if isinstance(parsed_result, GroundedAnswer):
            content = render_grounded_answer(parsed_result)
            generation_trace["schema_valid"] = True
        elif isinstance(parsed_result, dict) and "conclusion" in parsed_result:
            try:
                grounded = GroundedAnswer.model_validate(parsed_result)
            except Exception as exc:
                generation_trace["schema_valid"] = False
                raise ChatProviderError("Structured chat output failed validation") from exc
            content = render_grounded_answer(grounded)
            generation_trace["schema_valid"] = True
        else:
            if structured:
                generation_trace["schema_valid"] = False
                generation_trace["outcome"] = "schema_error"
                generation_trace["duration_ms"] = round(
                    (time.perf_counter() - started) * 1000, 2
                )
                metrics.inc("generation_requests_total", outcome="schema_error")
                raise ChatProviderError("Chat provider returned non-schema output")
            content = getattr(result, "content", result)
            generation_trace["schema_valid"] = False
        response_metadata = getattr(raw_result, "response_metadata", {}) or {}
        usage_metadata = getattr(raw_result, "usage_metadata", {}) or {}
        usage = usage_metadata or response_metadata.get("token_usage") or response_metadata.get("usage") or {}
        if isinstance(usage, dict):
            generation_trace["usage"] = {
                str(key): int(value)
                for key, value in usage.items()
                if str(key) in {"input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens"}
                and isinstance(value, (int, float))
            }
        finish_reason = response_metadata.get("finish_reason") or response_metadata.get("stop_reason")
        if isinstance(finish_reason, str):
            generation_trace["finish_reason"] = finish_reason[:40]
        generation_trace["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
        if isinstance(content, str):
            generation_trace["outcome"] = "success"
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
            generation_trace["outcome"] = "success"
            metrics.observe("generation_duration_seconds", time.perf_counter() - started, outcome="success")
            if current_release and context and cache_allowed and value:
                self._store_answer_cache(answer_key, value)
            return value
        metrics.inc("generation_requests_total", outcome="empty")
        generation_trace["outcome"] = "empty"
        metrics.observe("generation_duration_seconds", time.perf_counter() - started, outcome="empty")
        return ""

    def _store_answer_cache(self, key: tuple[tuple[object, ...], str], answer: str) -> None:
        if len(self._answer_cache) >= 128:
            oldest = min(self._answer_cache, key=lambda item: self._answer_cache[item][1])
            self._answer_cache.pop(oldest, None)
        self._answer_cache[key] = (answer, time.monotonic())

    async def readiness(self) -> dict[str, bool]:
        """Return a short-lived coalesced dependency probe.

        A load balancer/orchestrator may poll readiness concurrently. Re-running full
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
                if qdrant_contract_ready:
                    vector_store = self._get_vector_store()
                    locator = str(qdrant_contract.get("locator") or "").strip()
                    if locator:
                        vector_store.set_collection(locator)
                    checks["qdrant"] = await asyncio.wait_for(
                        vector_store.readiness(dataset_id=release[0], expected_points=release[1]),
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

    async def prewarm(self, *, release_id: str | None = None) -> None:
        """Open clients and warm a selected immutable release when possible."""
        try:
            qdrant_locator = ""
            async with session_scope() as session:
                await session.execute(text("SELECT 1"))
                # Resolve and retain the immutable release before the first
                # user request.  On a sleeping managed Postgres instance the
                # release lookup can otherwise consume most of the topical
                # route budget and force a false empty-evidence fallback.
                release = await GraphRepository(session).current_dataset_release()
                if release_id and release_id != (release[0] if release else ""):
                    # Evaluation may target an inactive immutable snapshot. It
                    # is still safe to warm its projection without changing
                    # the database active pointer.
                    projection_result = await session.execute(
                        text(
                            """
                            SELECT expected_count
                            FROM release_projections
                            WHERE dataset_id = :dataset_id
                              AND projection_kind IN ('qdrant', 'semantic')
                            ORDER BY CASE WHEN projection_kind = 'qdrant' THEN 0 ELSE 1 END
                            LIMIT 1
                            """
                        ),
                        {"dataset_id": release_id},
                    )
                    expected = projection_result.scalar_one_or_none()
                    if expected is not None:
                        release = (release_id, int(expected))
                if release is not None:
                    locator_result = await session.execute(
                        text(
                            """
                            SELECT locator
                            FROM release_projections
                            WHERE dataset_id = :dataset_id
                              AND projection_kind = 'qdrant'
                            LIMIT 1
                            """
                        ),
                        {"dataset_id": release[0]},
                    )
                    qdrant_locator = str(locator_result.scalar_one_or_none() or "").strip()
                if release is not None:
                    self._active_release = (*release, time.monotonic())
            if get_settings().qdrant_url and get_settings().qdrant_api_key:
                store = self._get_vector_store()
                if self._active_release is not None:
                    if qdrant_locator:
                        store.set_collection(qdrant_locator)
                    await asyncio.wait_for(
                        self._resolve_qdrant_release(
                            dataset_id=self._active_release[0],
                            expected_points=self._active_release[1],
                            preferred_collection=qdrant_locator,
                        ),
                        timeout=5,
                    )
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
        self._rewrite_cache.clear()
        self._answer_cache.clear()
        self._community_index_cache = None
        self._experience_index_cache = None
        self._authority_document_cache.clear()


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


def _apply_document_ranking_metadata(
    evidence: Sequence[RetrievalResult], metadata: dict[str, dict[str, object]]
) -> None:
    """Attach private canonical metadata used for ranking and public citations."""
    fields = (
        "document_number",
        "document_type",
        "issued_date",
        "effective_from",
        "effective_to",
        "legal_status",
        "legal_status_verified",
        "issuer",
        "jurisdiction",
        "source_url",
        "source_checked_at",
        "categories",
    )
    for item in evidence:
        values = metadata.get(item.document_id)
        if not values:
            continue
        for field in fields:
            setattr(item, field, values.get(field, getattr(item, field)))


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
    reference = (
        f"văn bản số hiệu {document.so_ky_hieu}"
        if document.so_ky_hieu
        else "văn bản được hỏi"
    )
    sentence_reference = reference[0].upper() + reference[1:]
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
            focused.append(f"Tên đầy đủ của {reference} là: {document.title}.")
        if asks_status:
            if document.ngay_co_hieu_luc:
                focused.append(
                    f"{sentence_reference} có hiệu lực từ ngày {document.ngay_co_hieu_luc} "
                    f"và có tình trạng {status}."
                )
            else:
                focused.append(f"{sentence_reference} có tình trạng {status}.")
        if asks_issue_date and document.ngay_ban_hanh:
            focused.append(f"{sentence_reference} được ban hành ngày {document.ngay_ban_hanh}.")
        if asks_category and category_values:
            focused.append(
                f"Nhóm nội dung của {reference} là {category_values[-1]}."
            )
        if focused:
            return " ".join(focused)

    values: list[str] = [f"{sentence_reference}: {document.title}."]
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
                f"Phân loại: {sentence_reference} thuộc nhóm {category_values[-1]}."
            )
    return " ".join(values)


def _answer_format_instruction(query: str) -> str:
    """Keep generation concise and deterministic by retrieval intent."""
    synthesis_rule = (
        "Không chép nguyên văn nguồn dài, không lặp lại cùng một ý, "
        "không trả tiêu đề/đoạn văn như chunk; hãy tổng hợp ý nghĩa pháp lý. "
    )
    intent = retrieval_intent(query)
    route = build_route_plan(query, settings=get_settings()).route
    if intent in {"lookup", "legal_unit"}:
        return synthesis_rule + "Trả lời tối đa 5 gạch đầu dòng, nêu đúng điều/khoản và không suy diễn."
    if intent == "temporal":
        return synthesis_rule + "Trả lời theo mốc thời gian: văn bản, ngày, trạng thái và nguồn; tối đa 6 gạch đầu dòng."
    if intent == "relational":
        return synthesis_rule + "Nêu quan hệ nguồn → đích và ý nghĩa được nguồn pháp lý xác nhận; tối đa 6 gạch đầu dòng."
    if route == "table":
        return synthesis_rule + (
            "Nếu nguồn có tỷ lệ, số tiền hoặc thời hạn, phải nêu trực tiếp giá trị đó "
            "kèm điều kiện áp dụng; phân biệt rõ tỷ lệ trên mức hưởng với tỷ lệ trên "
            "toàn bộ chi phí và không được bỏ qua con số đã có trong nguồn. "
            "Nếu thiếu một biến quyết định (ví dụ nội trú/ngoại trú), nêu các nhánh "
            "được nguồn xác nhận thay vì chỉ trả lời rằng chưa biết; tối đa 8 gạch đầu dòng."
        )
    return synthesis_rule + "Trả lời ngắn gọn trong tối đa 8 gạch đầu dòng; nếu nguồn chưa đủ hãy nói rõ giới hạn."


def render_grounded_answer(answer: GroundedAnswer) -> str:
    """Render the strict model contract into concise user-facing Vietnamese."""
    sections = [answer.conclusion.strip()]
    if answer.conditions:
        sections.append("Điều kiện:\n" + "\n".join(f"- {item}" for item in answer.conditions))
    if answer.exceptions:
        sections.append("Ngoại lệ:\n" + "\n".join(f"- {item}" for item in answer.exceptions))
    if answer.uncertainty:
        sections.append(f"Giới hạn thông tin: {answer.uncertainty.strip()}")
    return "\n\n".join(section for section in sections if section).strip()


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
        trace=dict(bundle.trace),
    )


def _merge_bundles(
    bundles: Sequence[RetrievalBundle],
    *,
    channel_weights: dict[str, float] | None = None,
    limit: int | None = None,
    max_per_document: int | None = None,
) -> RetrievalBundle:
    """Merge sub-query results without allowing one fragment to dominate."""
    relations_by_id: dict[str, object] = {}
    direct_response = ""
    direct_citations: list[Citation] = []
    seen_citations: set[str] = set()
    traces = [bundle.trace for bundle in bundles if bundle.trace]
    for bundle in bundles:
        if bundle.direct_response and not direct_response:
            direct_response = bundle.direct_response
        for citation in bundle.direct_citations or []:
            if citation.chunk_id not in seen_citations:
                seen_citations.add(citation.chunk_id)
                direct_citations.append(citation)
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
            trace={"children": traces},
        )
    settings = get_settings()
    evidence = weighted_rrf(
        {f"query_{index}": bundle.evidence for index, bundle in enumerate(bundles)},
        limit=limit if limit is not None else settings.max_llm_evidence,
        max_per_document=(
            max_per_document if max_per_document is not None else settings.max_chunks_per_document
        ),
        channel_weights=channel_weights,
    )
    return RetrievalBundle(
        evidence=evidence,
        relations=list(relations_by_id.values()),
        direct_response="",
        direct_citations=direct_citations,
        trace={"children": traces},
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
    "render_grounded_answer",
    "get_runtime",
]
