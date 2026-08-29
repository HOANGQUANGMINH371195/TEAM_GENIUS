from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Sequence
from datetime import date
from functools import lru_cache
from uuid import uuid4

from src.agents.prompts import NO_EVIDENCE_RESPONSE, SYSTEM_PROMPT
from src.agents.state import AgentState
from src.config import get_settings
from src.domain.route_plan import build_route_plan
from src.integrations.langfuse import resolve_prompt
from src.models.graph import Citation, Entity, Relation, RetrievalResult
from src.services.chat import get_runtime
from src.services.claims import build_legal_claim, claim_dict
from src.services.planner import evidence_gap_plan, followup_queries
from src.services.request_router import classify_request, input_guardrail
from src.services.retrieval import (
    decompose_query,
    extract_query_terms,
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
_HIGH_RISK_MARKERS = (
    "hiệu lực", "bãi bỏ", "thay thế", "mức hưởng", "mức chi trả", "được chi trả",
    "bao nhiêu tiền", "thanh toán",
)
_OFFICIAL_STATUS_MARKERS = ("hiệu lực", "còn hiệu lực", "hết hiệu lực", "bãi bỏ", "thay thế")


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
    decision, decision_source = await classify_request(guard.query, settings=get_settings())
    metadata["model_route"] = decision.model_dump(mode="json")
    metadata["model_route_source"] = decision_source
    metadata["route_plan"] = {
        **build_route_plan(guard.query, settings=get_settings()).as_dict(),
        "route": decision.route,
        "risk": decision.risk,
        "model_route": True,
        "model_route_confidence": decision.confidence,
        "sub_tasks": decision.sub_tasks,
        "needs_table": decision.needs_table,
        "needs_calculator": decision.needs_calculator,
        "needs_graph": decision.needs_graph,
        "needs_current_law": decision.needs_current_law,
    }
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
    # Adaptive retrieval preserves the complete user question and pairs it
    # with a clause-shaped rewrite. Running deterministic decomposition first
    # used to discard cross-condition facts (e.g. “mức đóng *và* hỗ trợ”),
    # bypassing both HyDE and the current-law reranker.
    if requires_evidence_verification(query):
        # High-risk legal questions are one semantic unit. Splitting them
        # into fragments (for example, “5 năm liên tục” and “cùng chi trả”)
        # and merging independently ranked bundles can discard the clause
        # that satisfies both conditions. Preserve the complete question so
        # lexical, semantic and operative retrieval are fused once.
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
            "route_intent": retrieval_intent(query),
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
            if (not requires_evidence_verification(query) or metadata_shortcut)
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
    context = _pack_context(
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
    """Pack complete evidence blocks until the context budget is exhausted.

    A block is either included in full or omitted (except for the bounded
    per-passage excerpt), so the final character budget cannot cut a citation
    in the middle and make its source span ambiguous.
    """
    if budget <= 0:
        return ""
    parts: list[str] = []
    used = 0
    used_tokens = 0
    for index, item in enumerate(evidence, start=1):
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
            f"NGUỒN THỨ {index}\n"
            f"ƯU TIÊN NGỮ CẢNH: {index}\n"
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
    return "\n---\n".join(parts)


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
    if not requires_evidence_verification(query):
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
    source_response = _deterministic_source_rule_response(state.get("query", ""), evidence)
    # A deterministic extractive shortcut is safe only when one canonical
    # legal unit is available.  With multiple passages, forcing source text
    # directly into the response produces duplicated bullets/raw chunks;
    # let the configured model synthesize the bounded context instead.
    if source_response and len(evidence) == 1:
        return result(source_response)
    # Numeric/entitlement passages need synthesis: the source extractor is
    # intentionally not used as a public answer because even one long legal
    # unit can look like an unprocessed chunk.  The model receives the same
    # verified context and the guardrail still fails closed on unsupported
    # claims.
    # Legal-unit enumeration is extractive: render canonical labelled units
    # directly instead of spending an LLM call (and risking reordering or
    # inventing a missing item).  The guardrail still audits the resulting
    # claims and emits the same citation contract.
    if (
        retrieval_intent(state.get("query", "")) == "legal_unit"
        and not requires_evidence_verification(state.get("query", ""))
    ):
        return result(_deterministic_legal_unit_response(evidence))
    response = await get_runtime().generate(
        state.get("query", ""),
        state.get("context", ""),
        timeout_seconds=generation_timeout,
    )
    return result(response)


def _deterministic_source_rule_response(
    query: str, evidence: Sequence[RetrievalResult]
) -> str:
    """Render an unambiguous exclusion rule directly from a legal unit.

    Some statutes encode an exclusion as a labelled child unit whose own text
    is only the label.  When the parent heading explicitly says the units are
    not covered and the user asks about that exact unit, an LLM adds latency
    without adding interpretation.  The wording is assembled solely from
    the retrieved source; no document or answer mapping is encoded here.
    """
    normalized_query = " ".join(query.casefold().split())
    exclusion_intent = (
        "không được" in normalized_query
        or "không hưởng" in normalized_query
        or ("dịch vụ" in normalized_query and "chi trả" in normalized_query)
    )
    if not exclusion_intent:
        return ""
    query_tokens = {
        token.casefold()
        for token in _CLAIM_TOKEN.findall(query)
        if len(token) > 2 and token.casefold() not in _CLAIM_STOPWORDS
    }
    for item in evidence:
        source = " ".join((item.title, item.section_title, item.content)).strip()
        lowered = source.casefold()
        if "không được hưởng" not in lowered:
            continue
        source_tokens = {
            token.casefold()
            for token in _CLAIM_TOKEN.findall(source)
            if len(token) > 2 and token.casefold() not in _CLAIM_STOPWORDS
        }
        if len(query_tokens & source_tokens) < 2:
            continue
        label = " ".join((item.section_title or item.content).split())
        if not label:
            continue
        article = re.search(r"\bĐiều\s+\d+[a-zđ]?", source, flags=re.IGNORECASE)
        unit = re.match(r"\s*(\d+)[.)]", label)
        legal_pointer = ""
        if article and unit:
            legal_pointer = f" Căn cứ {article.group(0)} khoản {unit.group(1)}."
        return f"Theo nguồn pháp lý được cung cấp, {label} thuộc trường hợp không được hưởng BHYT.{legal_pointer}"
    return ""


def _deterministic_legal_unit_response(evidence: Sequence[RetrievalResult]) -> str:
    """Format enumerated legal units without an LLM round trip.

    Only source-backed text is rendered, with stable ordering and a hard
    output bound.  Duplicate units are removed by unit/chunk identity.
    """
    lines: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(evidence, start=1):
        identity = item.unit_id or item.chunk_id
        if not identity or identity in seen:
            continue
        text = " ".join(item.content.split())
        if not text:
            continue
        seen.add(identity)
        label = " ".join((item.section_title or "").split())
        if not label:
            label = f"Nội dung {index}"
        # Keep extractive lookup answers readable without echoing a full
        # passage.  Prefer complete sentences and cap each unit independently.
        if len(text) > 320:
            match = re.match(r"(.{80,320}?[.!?。！？])(?:\s|$)", text)
            text = match.group(1) if match else text[:320].rstrip() + "…"
        lines.append(f"- {label}: {text}")
        if len(lines) >= 8:
            break
    if not lines:
        return no_answer_response(reason="no_evidence")
    return "Các điều/khoản được nguồn pháp lý xác nhận:\n" + "\n".join(lines)


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


def _ensure_numeric_coverage(
    response: str, query: str, evidence: Sequence[RetrievalResult]
) -> tuple[str, bool]:
    """Keep a source-backed percentage visible in a numeric answer."""
    if build_route_plan(query, settings=get_settings()).route != "table":
        return response, False
    if re.search(r"\d+(?:[.,]\d+)?\s*%", response):
        return response, False
    query_tokens = {
        token.casefold()
        for token in _CLAIM_TOKEN.findall(query)
        if len(token) > 2 and token.casefold() not in _CLAIM_STOPWORDS
    }
    candidates: list[tuple[int, int, str]] = []
    for item in evidence:
        source = " ".join((item.section_title, item.content)).strip()
        for sentence in re.split(r"(?<=[.!?。！？;])\s+|\n+", source):
            sentence = " ".join(sentence.split())
            if not re.search(r"\d+(?:[.,]\d+)?\s*%", sentence) or len(sentence) < 12:
                continue
            tokens = {token.casefold() for token in _CLAIM_TOKEN.findall(sentence)}
            overlap = len(query_tokens & tokens)
            if overlap >= 2:
                candidates.append((overlap, -len(sentence), sentence[:480]))
    if not candidates:
        return response, False
    _, _, sentence = max(candidates)
    return f"{response}\n- Mức phần trăm được nguồn xác nhận: {sentence}".strip(), True


def _source_backed_fallback(
    query: str, evidence: Sequence[RetrievalResult]
) -> str:
    """Render a compact source excerpt when synthesis returns empty.

    The fallback is extractive and query-derived: it never supplies a missing
    amount or legal conclusion. It only keeps a citation-bearing answer when
    a model emits the generic empty-result sentence despite valid evidence.
    """
    terms = extract_query_terms(query, limit=16)
    if not terms:
        return ""
    candidates: list[tuple[int, int, int, str]] = []
    for item in evidence:
        source = " ".join((item.section_title, item.content)).strip()
        if not source:
            continue
        segments = [
            " ".join(segment.split())
            for segment in re.split(r"\n+|(?<=[.!?;])\s+", source)
            if segment.strip()
        ]
        for segment in segments:
            overlap = sum(term in segment.casefold() for term in terms)
            if overlap < 2:
                continue
            has_value = bool(
                re.search(
                    r"\d+(?:[.,]\d+)?\s*%|\b\d+\s*(?:đồng|năm|tháng|ngày)\b",
                    segment.casefold(),
                )
            )
            # Keep the safety fallback clearly shorter than a raw passage;
            # the normal model/renderer path remains responsible for synthesis.
            excerpt = segment[:180].rstrip()
            candidates.append((2 if has_value else 1, overlap, -len(segment), excerpt))
    if not candidates:
        return ""
    candidates.sort(key=lambda row: (-row[0], -row[1], row[2], row[3]))
    return (
        "Nguồn pháp lý hiện có ghi nhận: "
        + candidates[0][3]
        + "\nGiới hạn thông tin: nguồn được cung cấp chưa đủ dữ liệu để khẳng định mọi điều kiện hoặc số tiền cụ thể."
    )


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


def _select_supported_citations(
    citations: Sequence[Citation], response: str, query: str, *, limit: int = 6
) -> list[Citation]:
    """Keep a compact citation set that actually overlaps the answer.

    Deterministic extractive responses used to attach the whole retrieval
    bundle (often 12 citations), including unrelated neighbouring documents.
    This is a query/answer-derived precision filter: it scores overlap with
    the rendered response and the user's terms, preserves source order for
    ties, and never invents or rewrites a citation.
    """
    if not citations or limit <= 0:
        return []
    response_sequence = _CLAIM_TOKEN.findall(response.casefold())
    response_tokens = set(response_sequence)
    response_triples = set(
        zip(response_sequence, response_sequence[1:], response_sequence[2:])
    )
    scored: list[tuple[float, int, Citation]] = []
    for index, citation in enumerate(citations):
        source = " ".join(
            (citation.title, citation.section_title, citation.quote)
        ).casefold()
        source_sequence = _CLAIM_TOKEN.findall(source)
        source_tokens = set(source_sequence)
        source_triples = set(
            zip(source_sequence, source_sequence[1:], source_sequence[2:])
        )
        answer_overlap = len(response_tokens & source_tokens)
        # A citation must support what was actually rendered. Query overlap
        # alone is insufficient because broad legal terms (for example
        # “thanh toán” or “BHYT”) occur in many unrelated provisions. Requiring
        # two answer tokens removes neighbouring retrieval noise while keeping
        # extractive source responses citeable.
        triple_overlap = len(response_triples & source_triples)
        if triple_overlap or answer_overlap >= 4:
            score = float(3 * triple_overlap + answer_overlap)
            scored.append((score, index, citation))
    if not scored:
        return []
    scored.sort(key=lambda item: (-item[0], item[1]))
    selected = [citation for _, _, citation in scored[:limit]]
    return selected[:limit]


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


def _audit_claims(response: str, citations: Sequence[Citation], query: str = "") -> list[dict]:
    """Create a conservative claim-to-evidence audit without trusting the LLM.

    This is a conservative lexical/fact entailment pre-check, not an
    open-ended semantic proof. High-risk routes fail closed when a claim cannot
    be tied to citation overlap or its concrete number/status conflicts with
    evidence; a stronger model verifier can be added without changing the
    response contract.
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
    source_tokens = {citation_id: _claim_tokens(value) for citation_id, value in source_text.items()}
    claims: list[dict] = []
    for index, sentence in enumerate(sentences, start=1):
        tokens = _claim_tokens(sentence)
        best_id = ""
        best_overlap = 0
        for citation_id, evidence_tokens in source_tokens.items():
            if not _claim_facts_supported(sentence, [source_text[citation_id]]):
                continue
            overlap = len(tokens & evidence_tokens)
            if overlap > best_overlap:
                best_id, best_overlap = citation_id, overlap
        risk_markers = [marker for marker in _HIGH_RISK_MARKERS if marker in sentence.casefold()]
        requires_official_status = any(marker in query.casefold() for marker in _OFFICIAL_STATUS_MARKERS)
        official_status_supported = any(
            citation.evidence_kind == "document_metadata" and citation.provenance_verified
            for citation in citations
        )
        risk_supported = not risk_markers or any(
            all(marker in value for marker in risk_markers) for value in source_text.values()
        )
        if requires_official_status and risk_markers:
            risk_supported = risk_supported and official_status_supported
        if not tokens:
            verification, reason = "unsupported", "claim has no verifiable content"
        elif not risk_supported:
            verification, reason = "unsupported", "high-risk marker is absent from cited evidence"
        elif not best_id or not _claim_facts_supported(sentence, [source_text[best_id]]):
            verification, reason = "unsupported", "facts are not supported by one cited source"
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
        factuality = 1.0 if best_id and _claim_facts_supported(sentence, [source_text[best_id]]) else 0.0
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


def _retain_supported_claims(claims: Sequence[dict]) -> tuple[str, list[dict]]:
    """Keep only sentences tied to a concrete citation by the audit.

    Returning raw retrieval excerpts after a verification failure both leaks
    internal vocabulary and turns a ranking error into a misleading answer.
    Partial/unsupported sentences are therefore removed independently; if no
    sentence survives, the system abstains cleanly.
    """
    supported = [claim for claim in claims if claim.get("verification") == "entailed"]
    if not supported:
        return NO_EVIDENCE_RESPONSE, []
    return "\n".join(
        f"- {str(claim.get('text') or '').strip().strip('*_')}" for claim in supported
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
    if not response:
        response = NO_EVIDENCE_RESPONSE
    if response == NO_EVIDENCE_RESPONSE and evidence:
        # Keep a source-derived excerpt when a provider/model emits the
        # generic empty-result sentence after successful retrieval.
        fallback = _source_backed_fallback(state.get("query", ""), evidence)
        if fallback:
            response = fallback
    if _looks_like_raw_evidence(response, evidence):
        # Never expose an un-synthesized retrieval chunk.  The generation node
        # normally prevents this path; this second check protects against
        # stale caches/provider regressions and fails closed.
        response = no_answer_response(state.get("query", ""), reason="unverified")
    response, numeric_coverage_added = _ensure_numeric_coverage(
        response, state.get("query", ""), evidence
    )
    # Extractive/deterministic responses are built from the final evidence
    # list. A direct-citation shortcut may belong to an earlier document
    # anchor and causes the claim auditor to discard the correct sentence
    # during the guardrail pass. Rebuild citations from the same evidence for
    # source-derived output; reserve direct citations for provider answers.
    deterministic_response = response.startswith("-") or response.startswith("Các điều/khoản")
    citations = (
        _citations_from_evidence(evidence, preserve_order=deterministic_response)
        if deterministic_response
        else state.get("direct_citations") or _citations_from_evidence(evidence)
    )
    if deterministic_response:
        citations = _select_supported_citations(
            citations,
            response,
            state.get("query", ""),
            limit=min(6, get_settings().max_citations),
        )
    claims = _audit_claims(response, citations, state.get("query", ""))
    if evidence and not deterministic_response and any(
        claim["verification"] != "entailed" for claim in claims
    ):
        response, claims = _retain_supported_claims(claims)
    # An abstention is a statement about the absence of sufficient support.
    # Showing a residual, unrelated citation next to it is internally
    # contradictory and makes a failed retrieval look authoritative.
    if response == NO_EVIDENCE_RESPONSE:
        citations = []
        claims = []
    supported_ids = {
        evidence_id
        for claim in claims
        if claim.get("verification") == "entailed"
        for evidence_id in claim.get("evidence_ids", [])
    }
    if evidence and not deterministic_response:
        citations = [citation for citation in citations if citation.chunk_id in supported_ids]
    metadata = dict(state.get("metadata") or {})
    metadata.update(
        {
            "citation_count": len(citations),
            "claim_count": len(claims),
            "verified_claim_count": sum(
                claim.get("verification") == "entailed" for claim in claims
            ),
            "guardrail_response_chars": len(response),
            "numeric_coverage_added": numeric_coverage_added,
            "guardrail_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    )
    return {
        "response": response,
        "citations": [citation.model_dump() for citation in citations],
        "claims": claims,
        "metadata": metadata,
    }
