from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections.abc import Sequence
from datetime import date
from functools import lru_cache
from uuid import uuid4

from src.agents.prompts import NO_EVIDENCE_RESPONSE, SYSTEM_PROMPT
from src.agents.state import AgentState
from src.config import get_settings
from src.domain.route_plan import apply_model_route, build_route_plan
from src.integrations.langfuse import resolve_prompt
from src.models.graph import Citation, Entity, Relation, RetrievalResult
from src.services.chat import get_runtime
from src.services.claims import build_legal_claim, claim_dict
from src.services.planner import evidence_gap_plan, followup_queries
from src.services.request_router import classify_request, input_guardrail
from src.services.retrieval import (
    decompose_query,
    no_answer_response,
    requires_evidence_verification,
    rerank_legal_candidates,
    retrieval_intent,
)

_REASONING_BLOCK = re.compile(
    r"<\s*(?:thinking|analysis|chain_of_thought|reasoning)\b[^>]*>.*?"
    r"<\s*/\s*(?:thinking|analysis|chain_of_thought|reasoning)\s*>",
    flags=re.IGNORECASE | re.DOTALL,
)
_INTERNAL_CONTEXT_FIELD = re.compile(
    r"\b(?:EVIDENCE_ID|DOCUMENT_ID|DATASET|DOCUMENT|CHUNK|SOURCE_REF)\s*=\s*[^\s,;]+",
    flags=re.IGNORECASE,
)
_CLAIM_TOKEN = re.compile(r"[0-9A-Za-zÀ-ỹĐđ]+", flags=re.IGNORECASE)
_FACT_NUMBER = re.compile(r"\d+(?:[./%-]\d+)*", re.IGNORECASE)
_STATUS_POLARITIES = (
    ("hết hiệu lực", "còn hiệu lực"),
    ("không còn hiệu lực", "còn hiệu lực"),
    ("bãi bỏ", "còn hiệu lực"),
    ("thay thế", "còn hiệu lực"),
)
_CLAIM_STOPWORDS = {
    "và", "là", "có", "được", "cho", "của", "theo", "trong", "với", "từ", "này",
    "khi", "để", "một", "các", "những", "về", "không", "người", "việc", "tại", "đến",
    "thì", "bị", "sẽ", "đã", "hay", "hoặc", "nếu", "cần", "phải", "nên", "được",
}
_OFFICIAL_STATUS_MARKERS = ("hiệu lực", "còn hiệu lực", "hết hiệu lực", "bãi bỏ", "thay thế")


def _evidence_diagnostic(item: RetrievalResult, rank: int) -> dict[str, object]:
    """Return bounded lineage for offline evaluation without leaking DB keys.

    The diagnostic artifact is intentionally internal: public API responses
    still expose only source citations.  Hashing identifiers makes it possible
    to compare deduplication/authority behaviour across runs without emitting
    raw chunk or document primary keys into logs.
    """
    def digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12] if value else ""

    return {
        "rank": rank,
        "document_key": digest(item.document_id),
        "chunk_key": digest(item.chunk_id),
        "document_number": item.document_number,
        "title": item.title[:240],
        "section_title": item.section_title[:240],
        "channels": sorted(set(item.channels)),
        "score": round(float(item.score), 6),
        "rank_details": {key: round(float(value), 6) for key, value in item.rank_details.items()},
        "authority": {
            "status": item.legal_status,
            "status_verified": bool(item.legal_status_verified),
            "issuer": item.issuer,
            "jurisdiction": item.jurisdiction,
            "effective_from": item.effective_from,
            "effective_to": item.effective_to,
        },
        "provenance": {
            "dataset_present": bool(item.dataset_id),
            "source_span_present": item.source_start is not None and item.source_end is not None,
            "content_hash_present": bool(item.text_sha256),
        },
    }


async def intake_node(state: AgentState) -> dict:
    query = state.get("query", "").strip()
    if not query:
        return {"error": "Query must not be empty"}
    guard = input_guardrail(query)
    metadata = dict(state.get("metadata") or {})
    metadata.setdefault("trace_id", uuid4().hex)
    metadata["input_guardrail"] = "allow" if guard.allowed else guard.reason
    if not guard.allowed:
        return {
            "query": guard.query,
            "response": "Tôi chỉ hỗ trợ câu hỏi về BHYT, viện phí và văn bản pháp luật liên quan.",
            "metadata": metadata,
        }
    settings = get_settings()
    decision, decision_source = await classify_request(guard.query, settings=settings)
    metadata["model_route"] = decision.model_dump(mode="json")
    metadata["model_route_source"] = decision_source
    base_plan = build_route_plan(guard.query, settings=settings)
    route_plan = apply_model_route(
        base_plan,
        route=decision.route,
        risk=decision.risk,
        needs_graph=decision.needs_graph,
        settings=settings,
    )
    metadata["route_plan"] = {
        **route_plan.as_dict(),
        "model_route": True,
        "model_route_confidence": decision.confidence,
        "sub_tasks": decision.sub_tasks,
        "needs_table": decision.needs_table,
        "needs_calculator": decision.needs_calculator,
        "needs_graph": decision.needs_graph,
        "needs_current_law": decision.needs_current_law,
        "answer_requirements": decision.answer_requirements,
    }
    if decision.route == "policy" and decision.direct_response:
        return {"query": guard.query, "response": decision.direct_response, "metadata": metadata}
    # The plan carries budgets/provider permissions, never legal evidence.
    # Keeping it in metadata makes each route auditable without changing the
    # public response contract.
    return {"query": guard.query, "metadata": metadata}


async def extract_entities_node(state: AgentState) -> dict:
    query = state.get("query", "")
    return {"entities": [Entity(name=query, entity_type="query")] if query else []}


async def retrieve_vectors_node(state: AgentState) -> dict:
    query = state.get("query", "")
    runtime = get_runtime()
    started = time.perf_counter()
    subqueries = decompose_query(query)
    route_override = (state.get("metadata") or {}).get("route_plan")
    high_risk_route = str((route_override or {}).get("risk") or "").casefold() == "high"
    # Adaptive retrieval preserves the complete user question and pairs it
    # with a clause-shaped rewrite. Running deterministic decomposition first
    # used to discard cross-condition facts (e.g. “mức đóng *và* hỗ trợ”),
    # bypassing both HyDE and the current-law reranker.
    if high_risk_route:
        # Preserve the complete question and use the single bounded retrieval
        # plan.  The staged retriever already runs lexical+dense recall and
        # phrase expansion concurrently; a second HyDE pass here duplicates
        # provider work and can displace a decisive negation.
        bundle = await runtime.retrieve_bundle(query, route_plan_override=route_override)
    elif get_settings().query_rewrite_enabled:
        bundle = await runtime.retrieve_bundle_adaptive(query, route_plan_override=route_override)
    elif len(subqueries) > 1:
        bundle = await runtime.retrieve_bundle_many(subqueries)
    else:
        bundle = await runtime.retrieve_bundle(query, route_plan_override=route_override)
    evidence, relations = bundle.evidence, bundle.relations
    release_id = next((item.dataset_id for item in evidence if item.dataset_id), "")
    if not release_id:
        release_id = (runtime._active_release or ("", 0, 0.0))[0]
    experience_hints = runtime.experience_hints(query, release_id=release_id) if release_id else []
    planner_started = time.perf_counter()
    route_plan = (state.get("metadata") or {}).get("route_plan") or {}
    grounded_plan = evidence_gap_plan(
        query,
        evidence,
        enabled=get_settings().feature_planner_enabled,
    )
    planner_followup_count = 0
    planner_followup_outcome = "not_needed"
    planner_followup_started = time.perf_counter()
    # Grounded planning is deliberately restricted to routes where a
    # relationship/temporal gap can change the answer. Ordinary topical and
    # exact requests stay on the fast single-pass path.
    if (
        grounded_plan.enabled
        and len(grounded_plan.missing_facts) >= 2
        and route_plan.get("route") in {"relational", "temporal", "deep"}
    ):
        followups = followup_queries(query, grounded_plan)
        try:
            followup_bundle = await asyncio.wait_for(
                runtime.retrieve_bundle_many(followups),
                timeout=min(
                    3.0,
                    max(0.25, float(route_plan.get("retrieval_budget_ms", 3000)) / 1000),
                ),
            )
            by_chunk = {item.chunk_id: item for item in evidence}
            by_chunk.update({item.chunk_id: item for item in followup_bundle.evidence})
            evidence = rerank_legal_candidates(query, list(by_chunk.values()))[
                : get_settings().max_llm_evidence
            ]
            relations = [*relations, *followup_bundle.relations]
            planner_followup_count = len(followups)
            planner_followup_outcome = "success"
        except TimeoutError:
            planner_followup_outcome = "timeout"
        except Exception:
            # Follow-up retrieval is additive. A provider outage must not turn
            # the already valid preliminary evidence into a 503.
            planner_followup_outcome = "fallback"
    planner_followup_ms = round((time.perf_counter() - planner_followup_started) * 1000, 2)
    metadata_shortcut = bool(
        bundle.direct_response
        and bundle.direct_citations
        and all(
            citation.evidence_kind == "document_metadata"
            and citation.provenance_verified
            for citation in bundle.direct_citations
        )
    )
    metadata = dict(state.get("metadata") or {})
    metadata.update(
        {
            "route_intent": route_plan.get("route") or retrieval_intent(query),
            "subquery_count": len(subqueries),
            "retrieval_ms": round((time.perf_counter() - started) * 1000, 2),
            "candidate_count": len(evidence),
            "relation_count": len(relations),
            "retrieval_channels": sorted(
                {channel for item in evidence for channel in item.channels}
            ),
            "retrieval_trace": bundle.trace,
            "planner_followup_count": planner_followup_count,
            "planner_followup_outcome": planner_followup_outcome,
            "planner_followup_ms": planner_followup_ms,
            "planner_ms": round((time.perf_counter() - planner_started) * 1000, 2),
            "grounded_plan": grounded_plan.as_dict(),
            "experience_hint_count": len(experience_hints),
            # Internal-only lineage used by eval/observability.  The API
            # deliberately strips metadata and continues returning public
            # citations only.
            "evidence_diagnostics": [
                _evidence_diagnostic(item, index)
                for index, item in enumerate(evidence, start=1)
            ],
        }
    )
    return {
        "vector_results": [item for item in evidence if "semantic" in item.channels],
        "graph_results": relations,
        "retrieved_evidence": evidence,
        # Direct responses are metadata/lookup shortcuts. For high-risk
        # legal questions, always pass evidence through the source extractor
        # and guardrail so required conditions cannot be hidden in a provider
        # shortcut.
        "response": (
            bundle.direct_response
            if (not high_risk_route or metadata_shortcut)
            else ""
        ),
        "direct_citations": bundle.direct_citations or [],
        "metadata": metadata,
        "experience_hints": experience_hints,
    }


def _relation_context(relation: Relation) -> str:
    # Storage/graph identifiers never enter the model context.  Relationship
    # descriptions are useful legal facts; database topology is not.
    return (
        f"QUAN HỆ PHÁP LÝ: {relation.relation_type}. "
        f"{relation.description}".strip()
    )


async def assemble_context_node(state: AgentState) -> dict:
    started = time.perf_counter()
    evidence: list[RetrievalResult] = state.get("retrieved_evidence", [])
    relations: list[Relation] = state.get("graph_results", [])
    settings = get_settings()
    route_plan = (state.get("metadata") or {}).get("route_plan") or {}
    route_context_budget = route_plan.get("context_budget")
    if not isinstance(route_context_budget, int) or route_context_budget <= 0:
        route_context_budget = settings.max_context_chars
    context, context_evidence_ids = _pack_context_bundle(
        evidence,
        relations,
        min(settings.max_context_chars, route_context_budget),
        token_budget=settings.max_context_tokens,
        model=settings.model_name,
    )
    metadata = dict(state.get("metadata") or {})
    metadata.update(
        {
            "context_ms": round((time.perf_counter() - started) * 1000, 2),
            "context_chars": len(context),
            "context_tokens": _count_tokens(context, settings.model_name),
            # Internal mapping for the model's bounded ``source_numbers``
            # contract. Public responses never expose storage identifiers.
            "context_evidence_ids": context_evidence_ids,
        }
    )
    return {"context": context, "metadata": metadata}


def _pack_context(
    evidence: Sequence[RetrievalResult],
    relations: Sequence[Relation],
    budget: int,
    *,
    token_budget: int | None = None,
    model: str = "",
) -> str:
    context, _ = _pack_context_bundle(
        evidence,
        relations,
        budget,
        token_budget=token_budget,
        model=model,
    )
    return context


def _pack_context_bundle(
    evidence: Sequence[RetrievalResult],
    relations: Sequence[Relation],
    budget: int,
    *,
    token_budget: int | None = None,
    model: str = "",
) -> tuple[str, list[str]]:
    """Pack complete evidence blocks until the context budget is exhausted.

    A block is either included in full or omitted (except for the bounded
    per-passage excerpt), so the final character budget cannot cut a citation
    in the middle and make its source span ambiguous.
    """
    if budget <= 0:
        return "", []
    parts: list[str] = []
    evidence_ids: list[str] = []
    used = 0
    used_tokens = 0
    seen_evidence: set[tuple[str, str]] = set()
    source_index = 0
    for item in evidence:
        # Fusion can return the same canonical legal unit through lexical,
        # dense and graph channels.  Sending it repeatedly wastes tokens and
        # makes the model over-copy one passage.  Dedupe on stable provenance,
        # falling back to normalized public content for legacy rows.
        identity = (
            item.document_id,
            item.unit_id or item.chunk_id or " ".join(item.content.casefold().split()),
        )
        if identity in seen_evidence:
            continue
        seen_evidence.add(identity)
        metadata_lines = []
        if item.document_type:
            metadata_lines.append(f"LOẠI VĂN BẢN: {item.document_type}")
        if item.effective_from:
            metadata_lines.append(f"HIỆU LỰC TỪ: {item.effective_from}")
        if item.effective_to:
            metadata_lines.append(f"HIỆU LỰC ĐẾN: {item.effective_to}")
        if item.legal_status_verified and item.legal_status:
            metadata_lines.append(f"TÌNH TRẠNG ĐÃ KIỂM TRA: {item.legal_status}")
        metadata = "\n".join(metadata_lines)
        if metadata:
            metadata += "\n"
        block = (
            f"NGUỒN THỨ {source_index + 1}\n"
            f"ƯU TIÊN NGỮ CẢNH: {source_index + 1}\n"
            f"TÊN VĂN BẢN: {item.title}\n"
            f"SỐ/KÝ HIỆU CÔNG KHAI: {item.document_number}\n"
            f"{metadata}"
            f"ĐIỀU/MỤC: {item.section_title}\n"
            f"NỘI DUNG: {item.content[:2000]}"
        )
        separator = "\n---\n" if parts else ""
        block_tokens = _count_tokens(block, model) if token_budget is not None else 0
        separator_tokens = _count_tokens(separator, model) if token_budget is not None else 0
        if used + len(separator) + len(block) > budget or (
            token_budget is not None and used_tokens + separator_tokens + block_tokens > token_budget
        ):
            break
        parts.append(block)
        source_index += 1
        evidence_ids.append(item.chunk_id)
        used += len(separator) + len(block)
        used_tokens += separator_tokens + block_tokens
    for relation in relations:
        block = _relation_context(relation)
        separator = "\n---\n" if parts else ""
        block_tokens = _count_tokens(block, model) if token_budget is not None else 0
        separator_tokens = _count_tokens(separator, model) if token_budget is not None else 0
        if used + len(separator) + len(block) > budget or (
            token_budget is not None and used_tokens + separator_tokens + block_tokens > token_budget
        ):
            break
        parts.append(block)
        used += len(separator) + len(block)
        used_tokens += separator_tokens + block_tokens
    return "\n---\n".join(parts), evidence_ids


@lru_cache(maxsize=8)
def _get_encoder(model: str):
    try:
        import tiktoken

        return tiktoken.encoding_for_model(model or "gpt-4o-mini")
    except Exception:
        return None


def _count_tokens(value: str, model: str) -> int:
    encoder = _get_encoder(model)
    if encoder is None:
        return max(1, (len(value) + 3) // 4)
    return len(encoder.encode(value))


async def verify_evidence_node(state: AgentState) -> dict:
    """Fail closed for high-risk claims when no release-scoped evidence survived retrieval."""
    started = time.perf_counter()
    query = state.get("query", "")
    evidence: list[RetrievalResult] = state.get("retrieved_evidence", [])
    direct_citations: list[Citation] = state.get("direct_citations", [])
    metadata = dict(state.get("metadata") or {})
    metadata["verification_evidence_count"] = len(evidence)
    metadata["verification_ms"] = 0.0
    route_plan = metadata.get("route_plan") or {}
    strict_verification = (
        route_plan.get("verifier_policy") == "strict"
        if route_plan
        else requires_evidence_verification(query)
    )
    if not strict_verification:
        metadata["verification_failed"] = False
        metadata["verification_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return {"verification_failed": False, "metadata": metadata}
    valid = [
        item for item in evidence
        if item.dataset_id and item.document_id and item.source_start is not None and item.source_end is not None
    ]
    official_status = any(
        citation.evidence_kind == "document_metadata" and citation.provenance_verified
        for citation in direct_citations
    )
    # A question that names an old year but asks for the rule "currently"
    # needs an explicit currentness check.  Do not let a semantically similar
    # newer passage silently answer a historical-instrument question.  The
    # requested year and the candidate instrument metadata are all query/source
    # derived; no document or answer mapping is involved.
    requested_years = [
        int(value) for value in re.findall(r"\b(?:19|20)\d{2}\b", query)
    ]
    asks_current_rule = any(
        marker in query.casefold() for marker in ("hiện nay", "hiện hành", "hiện tại")
    )
    if requested_years and asks_current_rule and max(requested_years) < date.today().year:
        requested_year = max(requested_years)
        matching_instrument = [
            item
            for item in evidence
            if requested_year
            in {
                int(value)
                for value in re.findall(
                    r"\b(?:19|20)\d{2}\b",
                    " ".join((item.issued_date, item.effective_from, item.document_number, item.title)),
                )
            }
        ]
        if not matching_instrument or not any(item.legal_status_verified for item in matching_instrument):
            metadata["verification_failed_reason"] = "historical_currentness_unverified"
            metadata["verification_failed"] = True
            return {
                "verification_failed": True,
                "response": no_answer_response(query, reason="unverified"),
                "metadata": {**metadata, "verification_ms": round((time.perf_counter() - started) * 1000, 2)},
            }
    if any(marker in query.casefold() for marker in _OFFICIAL_STATUS_MARKERS) and not official_status:
        metadata["verification_failed"] = True
        return {
            "verification_failed": True,
            "response": no_answer_response(query, reason="unverified"),
            "metadata": {**metadata, "verification_ms": round((time.perf_counter() - started) * 1000, 2)},
        }
    if valid or official_status:
        metadata["verification_failed"] = False
        metadata["verification_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return {"verification_failed": False, "metadata": metadata}
    metadata["verification_failed"] = True
    metadata["verification_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return {"verification_failed": True, "response": no_answer_response(query, reason="unverified"), "metadata": metadata}


async def generate_node(state: AgentState) -> dict:
    started = time.perf_counter()
    route_plan = (state.get("metadata") or {}).get("route_plan") or {}
    generation_budget_ms = route_plan.get("generation_budget_ms")
    generation_timeout = (
        max(0.25, float(generation_budget_ms) / 1000)
        if isinstance(generation_budget_ms, (int, float)) and generation_budget_ms > 0
        else None
    )

    def result(response: str) -> dict:
        metadata = dict(state.get("metadata") or {})
        metadata["generation_ms"] = round((time.perf_counter() - started) * 1000, 2)
        if generation_timeout is not None:
            metadata["generation_budget_ms"] = round(generation_timeout * 1000, 2)
        runtime = get_runtime()
        _, prompt_version = resolve_prompt(SYSTEM_PROMPT)
        metadata.setdefault("model_version", get_settings().model_name)
        metadata.setdefault("prompt_version", prompt_version)
        metadata.setdefault(
            "release_id",
            next(
                (item.dataset_id for item in state.get("retrieved_evidence", []) if item.dataset_id),
                (runtime._active_release[0] if runtime._active_release else ""),
            ),
        )
        trace_value = runtime.generation_trace() if hasattr(runtime, "generation_trace") else None
        if isinstance(trace_value, dict) and trace_value:
            metadata["generation_trace"] = trace_value
            for key in ("model_version", "prompt_version", "release_id"):
                if trace_value.get(key):
                    metadata[key] = trace_value[key]
        else:
            metadata["generation_trace"] = {
                "stage": "generation",
                "outcome": "skipped",
            }
        return {"response": response, "metadata": metadata}

    if state.get("response"):
        return result(state["response"])
    evidence: list[RetrievalResult] = state.get("retrieved_evidence", [])
    if not evidence:
        return result(no_answer_response(state.get("query", "")))
    # Every evidence-backed legal answer goes through the same bounded model
    # synthesis.  Earlier single-passage/exclusion shortcuts selected wording
    # from query phrases and could echo a legal unit instead of answering a
    # paraphrase. Metadata-only lookups are still allowed to return the
    # repository's typed direct response before this node.
    response = await get_runtime().generate(
        state.get("query", ""),
        state.get("context", ""),
        timeout_seconds=generation_timeout,
        route_plan_override=route_plan,
    )
    return result(response)


def _sanitize_output(value: str, evidence: Sequence[RetrievalResult] = ()) -> str:
    sanitized = _REASONING_BLOCK.sub("", value).strip()
    sanitized = re.sub(r"^\s*(?:<\/?(?:thinking|analysis|reasoning)>)+\s*", "", sanitized, flags=re.I)
    sanitized = _INTERNAL_CONTEXT_FIELD.sub("", sanitized)
    # Defence in depth for stale cached/provider output that copied an opaque
    # identifier in prose. Replace exact tokens only, never substrings of a
    # public legal number, date, percentage or monetary value.
    replacements: dict[str, str] = {}
    for item in evidence:
        public_label = item.document_number or item.title or "nguồn pháp lý"
        if len(item.document_id) >= 5:
            replacements[item.document_id] = public_label
        if len(item.chunk_id) >= 5:
            replacements[item.chunk_id] = "nguồn pháp lý"
        if len(item.dataset_id) >= 5:
            replacements[item.dataset_id] = ""
    for private_id in sorted(replacements, key=len, reverse=True):
        sanitized = re.sub(
            rf"(?<![\w./-]){re.escape(private_id)}(?![\w./-])",
            replacements[private_id],
            sanitized,
        )
    return sanitized.strip()


def _looks_like_raw_evidence(value: str, evidence: Sequence[RetrievalResult]) -> bool:
    """Detect a provider/extractive response that is effectively one chunk.

    This is a safety invariant, not a quality score: a long answer whose
    token set is almost identical to one retrieved passage is withheld rather
    than shown as if it were a synthesized conclusion.  Short legal quotes
    remain allowed because they are intentionally bounded by the source.
    """
    if len(value) < 360 or not evidence:
        return False
    response_tokens = set(_CLAIM_TOKEN.findall(value.casefold()))
    if len(response_tokens) < 8:
        return False
    lines = [line.strip(" -*") for line in value.splitlines() if line.strip()]
    for item in evidence:
        source_tokens = set(
            _CLAIM_TOKEN.findall(f"{item.section_title} {item.content}".casefold())
        )
        if not source_tokens:
            continue
        overlap = len(response_tokens & source_tokens) / len(response_tokens)
        if overlap >= 0.90:
            return True
        for line in lines:
            if len(line) < 240:
                continue
            line_tokens = set(_CLAIM_TOKEN.findall(line.casefold()))
            if len(line_tokens) >= 20 and len(line_tokens & source_tokens) / len(line_tokens) >= 0.88:
                return True
    return False


def _deduplicate_response_lines(value: str) -> str:
    """Remove exact repeated bullets/sentences without rewriting content."""
    seen: set[str] = set()
    kept: list[str] = []
    for line in value.splitlines():
        normalized = " ".join(line.casefold().split())
        if normalized and normalized in seen:
            continue
        if normalized:
            seen.add(normalized)
        kept.append(line.rstrip())
    return "\n".join(kept).strip()


def _citations_from_evidence(
    evidence: list[RetrievalResult], *, preserve_order: bool = False
) -> list[Citation]:
    from src.config import get_settings

    citations: list[Citation] = []
    seen: set[str] = set()
    ranked = list(evidence) if preserve_order else sorted(
        evidence,
        key=lambda item: (-float(item.score), str(item.chunk_id)),
    )
    for item in ranked:
        if not item.chunk_id or item.chunk_id in seen:
            continue
        seen.add(item.chunk_id)
        citations.append(
            Citation(
                document_id=item.document_id,
                chunk_id=item.chunk_id,
                dataset_id=item.dataset_id,
                title=item.title,
                document_number=item.document_number,
                section_title=item.section_title,
                quote=item.content[:600],
                channels=item.channels,
                evidence_kind="legal_unit" if "page_index" in item.channels else "passage",
                source_start=item.source_start,
                source_end=item.source_end,
                text_sha256=item.text_sha256,
                provenance_verified=item.legal_status_verified,
                source_url=item.source_url,
                source_checked_at=item.source_checked_at,
            )
        )
        if len(citations) >= get_settings().max_citations:
            break
    return citations


def _claim_tokens(value: str) -> set[str]:
    return {
        token.casefold()
        for token in _CLAIM_TOKEN.findall(value)
        if token.casefold() not in _CLAIM_STOPWORDS and len(token) > 1
    }


def _claim_facts_supported(claim: str, evidence: Sequence[str]) -> bool:
    """Reject concrete numeric/status contradictions in a cited claim.

    Token overlap can accept a sentence with a changed date, percentage, or
    legal-status polarity. This bounded deterministic check is conservative;
    it strengthens the lexical audit without pretending to be open-ended
    semantic proof.
    """
    claim_text = claim.casefold()
    evidence_text = " ".join(evidence).casefold()
    def normalized_numbers(value: str) -> set[str]:
        normalized: set[str] = set()
        for number in _FACT_NUMBER.findall(value):
            # Legal text frequently alternates between ``5``/``05`` and
            # ``6``/``06``.  Compare numeric identity, not presentation, while
            # preserving compound tokens such as dates and percentages.
            normalized.add(
                ".".join(
                    part.lstrip("0") or "0"
                    for part in re.split(r"(?=[./%-])|(?<=[./%-])", number)
                )
            )
        return normalized

    if not normalized_numbers(claim_text).issubset(normalized_numbers(evidence_text)):
        return False
    for positive, negative in _STATUS_POLARITIES:
        if positive in claim_text and negative in evidence_text and positive not in evidence_text:
            return False
        if negative in claim_text and positive in evidence_text and negative not in evidence_text:
            return False
    return True


def _audit_claims(
    response: str,
    citations: Sequence[Citation],
    query: str = "",
    *,
    model_source_ids: set[str] | None = None,
    model_source_text: dict[str, str] | None = None,
) -> list[dict]:
    """Create a bounded claim-to-source audit.

    A structured synthesis must explicitly select the numbered context sources
    it used.  That source contract allows legitimate semantic paraphrases to
    survive without a second model call, while numeric and legal-status facts
    are still checked deterministically. Legacy/plain-text provider output uses
    the stricter lexical fallback because it carries no source selection.
    """
    sentences = [
        sentence.strip(" -*•\t")
        for sentence in re.split(r"(?<=[.!?。！？])\s+|\n+", response)
        if sentence.strip(" -*•\t")
    ]
    source_text = {
        citation.chunk_id: " ".join((citation.title, citation.section_title, citation.quote)).casefold()
        for citation in citations
    }
    if model_source_text:
        source_text.update(
            {
                chunk_id: value.casefold()
                for chunk_id, value in model_source_text.items()
                if chunk_id in source_text
            }
        )
    source_tokens = {citation_id: _claim_tokens(value) for citation_id, value in source_text.items()}
    selected_source_text = [
        source_text[citation_id]
        for citation_id in (model_source_ids or set())
        if citation_id in source_text
    ]
    claims: list[dict] = []
    for index, sentence in enumerate(sentences, start=1):
        tokens = _claim_tokens(sentence)
        best_id = ""
        best_overlap = 0
        facts_supported_by_contract = bool(
            model_source_ids is not None
            and selected_source_text
            and _claim_facts_supported(sentence, selected_source_text)
        )
        for citation_id, evidence_tokens in source_tokens.items():
            if (
                model_source_ids is None
                and not _claim_facts_supported(sentence, [source_text[citation_id]])
            ):
                continue
            overlap = len(tokens & evidence_tokens)
            if overlap > best_overlap:
                best_id, best_overlap = citation_id, overlap
        if model_source_ids is not None and facts_supported_by_contract and not best_id:
            best_id = next(
                (
                    citation.chunk_id
                    for citation in citations
                    if citation.chunk_id in model_source_ids
                ),
                "",
            )
        requires_official_status = any(marker in query.casefold() for marker in _OFFICIAL_STATUS_MARKERS)
        official_status_supported = any(
            citation.evidence_kind == "document_metadata" and citation.provenance_verified
            for citation in citations
        )
        if not tokens:
            verification, reason = "unsupported", "claim has no verifiable content"
        elif requires_official_status and not official_status_supported:
            verification, reason = "unsupported", "official status provenance is unavailable"
        elif model_source_ids is not None and not facts_supported_by_contract:
            verification, reason = "unsupported", "numeric/status facts conflict with selected sources"
        elif not best_id or (
            model_source_ids is None
            and not _claim_facts_supported(sentence, [source_text[best_id]])
        ):
            verification, reason = "unsupported", "facts are not supported by one cited source"
        elif model_source_ids is not None and best_id in model_source_ids:
            verification, reason = (
                "entailed",
                "model-selected source with deterministic numeric/status validation",
            )
        elif best_overlap >= 2:
            verification, reason = "entailed", "lexical overlap with cited evidence"
        elif best_overlap == 1:
            verification, reason = "partial", "limited lexical overlap; review required"
        else:
            verification, reason = "unsupported", "no cited evidence overlap"
        best_citation = next(
            (citation for citation in citations if citation.chunk_id == best_id),
            None,
        )
        token_denominator = max(1, len(tokens))
        faithfulness = min(1.0, best_overlap / token_denominator)
        factuality = 1.0 if (
            facts_supported_by_contract
            or (best_id and _claim_facts_supported(sentence, [source_text[best_id]]))
        ) else 0.0
        if best_citation is not None and not best_citation.provenance_verified:
            factuality *= 0.75
        completeness = min(1.0, best_overlap / max(1, len(_claim_tokens(query)))) if query else faithfulness
        claims.append(
            claim_dict(
                build_legal_claim(
                    claim_id=f"claim-{index}",
                    text=sentence,
                    citation=best_citation,
                    verification=verification,
                    reason=reason,
                    faithfulness=faithfulness,
                    factuality=factuality,
                    completeness=completeness,
                )
            )
        )
    return claims


def _model_selected_citations(
    evidence: Sequence[RetrievalResult],
    context_evidence_ids: Sequence[str],
    source_numbers: Sequence[object],
) -> tuple[list[Citation], bool]:
    """Resolve public citations from model-selected source numbers.

    Returns ``valid=False`` if the model references a source outside the exact
    context it received. No opaque identifier is ever accepted from the model
    or emitted publicly.
    """
    if not context_evidence_ids or not source_numbers:
        return [], False
    resolved_ids: list[str] = []
    for value in source_numbers:
        if not isinstance(value, int) or isinstance(value, bool):
            return [], False
        index = value - 1
        if index < 0 or index >= len(context_evidence_ids):
            return [], False
        chunk_id = context_evidence_ids[index]
        if chunk_id not in resolved_ids:
            resolved_ids.append(chunk_id)
    by_chunk = {item.chunk_id: item for item in evidence if item.chunk_id}
    if any(chunk_id not in by_chunk for chunk_id in resolved_ids):
        return [], False
    selected = [by_chunk[chunk_id] for chunk_id in resolved_ids]
    return _citations_from_evidence(selected, preserve_order=True), True


def _retain_supported_claims(claims: Sequence[dict]) -> tuple[str, list[dict]]:
    """Keep only sentences tied to a concrete citation by the audit.

    Returning raw retrieval excerpts after a verification failure both leaks
    internal vocabulary and turns a ranking error into a misleading answer.
    Partial/unsupported sentences are therefore removed independently; if no
    sentence survives, the system abstains cleanly.
    """
    # Keep independently supported model sentences.  ``partial`` is retained
    # in diagnostics for review but is not strong enough to publish: a single
    # shared word can be incidental in a large legal corpus.
    supported = [
        claim for claim in claims
        if claim.get("verification") == "entailed"
        and claim.get("evidence_ids")
    ]
    if not supported:
        return NO_EVIDENCE_RESPONSE, []
    return "\n".join(
        str(claim.get("text") or "").strip().strip("*_") for claim in supported
    ), supported


def _normalize_response(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Sequence):
        return "".join(
            str(block.get("text", ""))
            for block in value
            if isinstance(block, dict) and block.get("type") == "text"
        ).strip()
    return ""


async def guardrail_node(state: AgentState) -> dict:
    started = time.perf_counter()
    evidence = state.get("retrieved_evidence", [])
    response = _deduplicate_response_lines(
        _sanitize_output(_normalize_response(state.get("response", "")), evidence)
    )
    forced_abstention = not response or response == NO_EVIDENCE_RESPONSE
    if not response:
        response = NO_EVIDENCE_RESPONSE
    # Retrieval excerpts are context, never a public answer.  If generation
    # abstains, keep the safe abstention instead of copying a source chunk.
    if _looks_like_raw_evidence(response, evidence):
        # Never expose an un-synthesized retrieval chunk.  The generation node
        # normally prevents this path; this second check protects against
        # stale caches/provider regressions and fails closed.
        response = no_answer_response(state.get("query", ""), reason="unverified")
        forced_abstention = True
    # Never append an extractive sentence to compensate for a missing number.
    # Numeric completeness belongs to retrieval + the single synthesis call;
    # copying a candidate passage here previously leaked raw chunks and could
    # turn a ranking mistake into a confident legal answer.
    numeric_coverage_added = False
    metadata = dict(state.get("metadata") or {})
    route_plan = metadata.get("route_plan") or {}
    generation_trace = metadata.get("generation_trace") or {}
    schema_source_contract = bool(generation_trace.get("schema_valid"))
    model_source_ids: set[str] | None = None
    model_source_text: dict[str, str] | None = None
    selected_model_citations: list[Citation] = []
    if schema_source_contract:
        selected_model_citations, source_contract_valid = _model_selected_citations(
            evidence,
            metadata.get("context_evidence_ids") or [],
            generation_trace.get("source_numbers") or [],
        )
        if not source_contract_valid:
            response = no_answer_response(state.get("query", ""), reason="unverified")
            forced_abstention = True
        else:
            model_source_ids = {
                citation.chunk_id for citation in selected_model_citations
            }
            model_source_text = {
                item.chunk_id: " ".join((item.title, item.section_title, item.content[:2000]))
                for item in evidence
                if item.chunk_id in model_source_ids
            }
    strict_verification = (
        route_plan.get("verifier_policy") == "strict"
        if route_plan
        else requires_evidence_verification(state.get("query", ""))
    )
    if forced_abstention:
        citations = []
    elif model_source_ids is not None:
        citations = selected_model_citations
    elif strict_verification:
        # Provider/direct citations may come from an earlier shortlist and
        # omit a governing authority that is present in the final evidence.
        # Rebuild from the verified bundle and reserve one slot for the best
        # primary/current source before applying the citation cap.
        verified_direct = [
            citation
            for citation in state.get("direct_citations", [])
            if citation.evidence_kind == "document_metadata"
            and citation.provenance_verified
        ]
        ordered = [
            *verified_direct,
            *_citations_from_evidence(evidence, preserve_order=True),
        ]
        seen_citations: set[str] = set()
        ordered = [
            citation
            for citation in ordered
            if citation.chunk_id not in seen_citations
            and not seen_citations.add(citation.chunk_id)
        ]
        authority_items = [
            item for item in evidence
            if item.document_number and item.source_start is not None and item.source_end is not None
        ]
        authority_items.sort(
            key=lambda item: (
                bool(item.legal_status_verified),
                any(marker in f"{item.document_type} {item.title}".casefold()
                    for marker in ("luật", "nghị định", "văn bản hợp nhất")),
                float(item.score),
            ),
            reverse=True,
        )
        if authority_items:
            authority_citation = _citations_from_evidence(authority_items[:1], preserve_order=True)
            if authority_citation:
                authority_id = authority_citation[0].chunk_id
                ordered = authority_citation + [item for item in ordered if item.chunk_id != authority_id]
        citations = ordered[: get_settings().max_citations]
    else:
        citations = state.get("direct_citations") or _citations_from_evidence(evidence)
    claims = [] if forced_abstention else _audit_claims(
        response,
        citations,
        state.get("query", ""),
        model_source_ids=model_source_ids,
        model_source_text=model_source_text,
    )
    if not forced_abstention and evidence and any(
        claim["verification"] != "entailed" for claim in claims
    ):
        response, claims = _retain_supported_claims(claims)
    # An abstention is a statement about the absence of sufficient support.
    # Showing a residual, unrelated citation next to it is internally
    # contradictory and makes a failed retrieval look authoritative.
    if forced_abstention or response == NO_EVIDENCE_RESPONSE:
        citations = []
        claims = []
    supported_ids = {
        evidence_id
        for claim in claims
        if claim.get("verification") == "entailed"
        for evidence_id in claim.get("evidence_ids", [])
    }
    if evidence and model_source_ids is None:
        citations = [citation for citation in citations if citation.chunk_id in supported_ids]
    metadata.update(
        {
            "citation_count": len(citations),
            "claim_count": len(claims),
            "verified_claim_count": sum(
                claim.get("verification") == "entailed" for claim in claims
            ),
            "guardrail_response_chars": len(response),
            "guardrail_evidence_count": len(evidence),
            "numeric_coverage_added": numeric_coverage_added,
            "source_contract": (
                "valid" if model_source_ids is not None
                else ("invalid" if schema_source_contract else "legacy")
            ),
            "guardrail_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    )
    return {
        "response": response,
        # Preserve the verified retrieval bundle through the terminal graph
        # node.  Some LangGraph state adapters otherwise serialize only the
        # citation subset, hiding valid authority evidence from downstream
        # clients and evaluation even though retrieval found it.
        "retrieved_evidence": evidence,
        "citations": [citation.model_dump() for citation in citations],
        "claims": claims,
        "metadata": metadata,
    }
