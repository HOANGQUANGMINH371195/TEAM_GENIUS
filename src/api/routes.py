from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from datetime import date
from html import escape

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, StreamingResponse

from src.agents.graph import get_agent
from src.agents.prompts import SYSTEM_PROMPT
from src.api.auth import get_current_user
from src.api.public_contract import public_citations
from src.application.adapters import LangGraphAgentAdapter
from src.application.answer import AnswerLegalQuestion, StreamLegalQuestion
from src.config import get_settings
from src.db.repositories import GraphRepository
from src.db.session import session_scope
from src.domain.route_plan import build_route_plan
from src.integrations.langfuse import configure_langfuse, resolve_prompt, trace_span, tracing_enabled
from src.models.schemas import (
    AgentStatusResponse,
    AnalyzeRequest,
    AnalyzeResponse,
    BenefitCalculationRequest,
    BenefitCalculationResponse,
    BenefitCalculationScenariosRequest,
    BenefitCalculationScenariosResponse,
    CalculatorDraftEvidence,
    CalculatorDraftRequest,
    CalculatorDraftResponse,
    CalculatorDraftValue,
    ChatRequest,
    ChatResponse,
    EligibilityChecklistRequest,
    EligibilityChecklistResponse,
    LegalTimelineResponse,
)
from src.services.calculator import CalculationInputError, calculate_bhyt_benefit
from src.services.chat import ChatProviderError, GraphRagUnavailableError, get_runtime
from src.services.conversation_cache import get_conversation_cache
from src.services.conversation_context import (
    apply_structured_user_facts,
    build_conversation_anchors,
    resolve_conversational_query,
)
from src.services.conversations import ConversationStoreError, get_conversation_store
from src.services.document_viewer import sanitize_document_html
from src.services.eligibility_checklist import ChecklistInputError, build_eligibility_checklist
from src.services.idempotency import IdempotencyDecision, get_idempotency_store
from src.services.legal_timeline import assemble_public_timeline
from src.services.metrics import metrics
from src.services.research_jobs import (
    RedisResearchJobQueue,
    ResearchJobQueue,
    ResearchQueueFullError,
    create_research_queue,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Agent"])
_research_queue: ResearchJobQueue | RedisResearchJobQueue | None = None


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "") or ""


def _idempotency_key(request: Request) -> str:
    return request.headers.get("Idempotency-Key", "").strip()


def _request_hash(*, owner_uid: str, endpoint: str, payload: object) -> str:
    canonical = json.dumps(
        {"owner_uid": owner_uid, "endpoint": endpoint, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _begin_idempotency(
    request: Request,
    *,
    owner_uid: str,
    endpoint: str,
    payload: object,
) -> tuple[str, IdempotencyDecision]:
    key = _idempotency_key(request)
    settings = get_settings()
    if not key:
        if settings.app_env == "production":
            raise HTTPException(
                status_code=400,
                detail="Idempotency-Key header is required for this operation",
            )
        return "", IdempotencyDecision("disabled", request_id=_request_id(request))
    request.state.idempotency_key = key
    decision = await get_idempotency_store().begin(
        owner_uid=owner_uid,
        endpoint=endpoint,
        key=key,
        request_hash=_request_hash(owner_uid=owner_uid, endpoint=endpoint, payload=payload),
        request_id=_request_id(request),
    )
    if decision.state == "conflict":
        raise HTTPException(status_code=409, detail="Idempotency key conflicts with another request")
    if decision.state == "in_progress":
        raise HTTPException(status_code=409, detail="A request with this idempotency key is still processing")
    return key, decision


async def _replay_stream(response: dict) -> AsyncIterator[str]:
    event_id = 0
    for event_type, payload in (
        ("status", {"stage": "replay"}),
        ("final", response),
        ("done", {"ok": True, "replayed": True}),
    ):
        event_id += 1
        yield _sse_event(event_type, payload, event_id=event_id)


def _get_research_queue() -> ResearchJobQueue | RedisResearchJobQueue:
    global _research_queue
    if _research_queue is None:
        _research_queue = create_research_queue()
    return _research_queue


async def close_research_queue() -> None:
    global _research_queue
    if _research_queue is not None:
        await _research_queue.close()
        _research_queue = None


def _trace_stage_metrics(result: dict) -> dict[str, object]:
    """Keep Langfuse stage telemetry numeric/route-scoped and secret-free."""
    metadata = result.get("metadata") if isinstance(result, dict) else None
    if not isinstance(metadata, dict):
        return {}
    allowed = (
        "route_intent", "retrieval_ms", "planner_ms", "verification_ms",
        "guardrail_ms", "generation_ms", "context_ms", "candidate_count",
        "relation_count", "planner_followup_count", "planner_followup_outcome",
        "model_version", "prompt_version", "release_id",
    )
    return {key: metadata[key] for key in allowed if key in metadata}


def _public_route(result: dict) -> str:
    """Return only the bounded route enum for browser/evidence telemetry."""
    metadata = result.get("metadata") if isinstance(result, dict) else None
    route_plan = metadata.get("route_plan") if isinstance(metadata, dict) else None
    route = route_plan.get("route") if isinstance(route_plan, dict) else ""
    return route if route in {"simple", "exact", "policy", "table", "topical", "temporal", "relational", "global", "deep"} else ""


async def _context_release_id() -> str:
    """Read the active release pointer for conversation-cache isolation.

    This is a tiny indexed metadata query, not a retrieval call.  Returning an
    empty ID on a transient metadata failure preserves the existing degraded
    behaviour; legal retrieval still enforces its own release boundary.
    """
    try:
        async with session_scope() as session:
            release = await GraphRepository(session).current_dataset_release()
            return release[0] if release else ""
    except Exception:
        logger.warning("Active release unavailable for conversation cache", exc_info=True)
        return ""


def _context_prompt_version() -> str:
    return resolve_prompt(SYSTEM_PROMPT)[1]


async def _recent_turns_for_request(*, owner_uid: str, conversation_id: str) -> list[dict]:
    release_id = await _context_release_id()
    prompt_version = _context_prompt_version()
    return await get_conversation_cache().get_or_load(
        owner_uid=owner_uid,
        conversation_id=conversation_id,
        release_id=release_id,
        prompt_version=prompt_version,
        loader=lambda: get_conversation_store().recent_turns(
            owner_uid=owner_uid,
            conversation_id=conversation_id,
            limit=get_settings().conversation_cache_max_turns,
        ),
    )


@router.post(
    "/calculator/bhyt",
    response_model=BenefitCalculationResponse,
    summary="Calculate a BHYT covered amount from verified rule facts",
)
async def calculate_bhyt(
    request: BenefitCalculationRequest,
    _user: dict = Depends(get_current_user),
) -> BenefitCalculationResponse:
    """Perform exact arithmetic; retrieval supplies the legal rule values.

    The endpoint deliberately does not select a percentage from the question.
    Callers must provide the selected rule and its provenance after retrieval
    and verification.
    """
    if not get_settings().feature_calculator_enabled:
        raise HTTPException(status_code=404, detail="Calculator unavailable")
    try:
        result = calculate_bhyt_benefit(
            covered_cost=request.covered_cost,
            base_rate_percent=request.base_rate_percent,
            copayment_spend=request.copayment_spend,
            copayment_threshold=request.copayment_threshold,
            continuous_years=request.continuous_years,
            required_years=request.required_years,
            threshold_rate_percent=request.threshold_rate_percent,
            rule_provenance=tuple(request.rule_provenance),
        )
    except CalculationInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return BenefitCalculationResponse(**result.as_dict())


@router.post(
    "/calculator/bhyt/scenarios",
    response_model=BenefitCalculationScenariosResponse,
    summary="Compare bounded BHYT calculation scenarios",
)
async def compare_bhyt_scenarios(
    request: BenefitCalculationScenariosRequest,
    _user: dict = Depends(get_current_user),
) -> BenefitCalculationScenariosResponse:
    """Run up to eight exact scenarios without an LLM or legal-rate lookup."""
    if not get_settings().feature_calculator_enabled:
        raise HTTPException(status_code=404, detail="Calculator unavailable")
    results: list[dict[str, object]] = []
    for index, scenario in enumerate(request.scenarios):
        try:
            result = calculate_bhyt_benefit(
                covered_cost=scenario.calculation.covered_cost,
                base_rate_percent=scenario.calculation.base_rate_percent,
                copayment_spend=scenario.calculation.copayment_spend,
                copayment_threshold=scenario.calculation.copayment_threshold,
                continuous_years=scenario.calculation.continuous_years,
                required_years=scenario.calculation.required_years,
                threshold_rate_percent=scenario.calculation.threshold_rate_percent,
                rule_provenance=tuple(scenario.calculation.rule_provenance),
            )
        except CalculationInputError as exc:
            raise HTTPException(status_code=422, detail=f"scenario {index + 1}: {exc}") from exc
        results.append({"label": scenario.label, "calculation": result.as_dict()})
    return BenefitCalculationScenariosResponse(results=results)


def _draft_number_values(text_value: str) -> list[tuple[str, str]]:
    """Extract explicit numeric literals from retrieved source text.

    This is an assistive projection only: it never assigns a legal rate to a
    scenario.  A user must choose which source-backed value applies before the
    exact Decimal calculator runs.
    """
    values: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for match in re.finditer(r"(?<![\d.,])(\d{1,3}(?:[.,]\d+)?)\s*%", text_value):
        number = match.group(1).replace(",", ".")
        key = (number, "percent")
        if key not in seen:
            seen.add(key)
            values.append(key)
    for match in re.finditer(
        r"(?<![\d.,])(\d[\d.,]*)\s*(tỷ|triệu|nghìn|đồng|vnđ|đ)\b",
        text_value.casefold(),
    ):
        raw, suffix = match.groups()
        compact = raw.replace(".", "").replace(",", ".")
        try:
            from decimal import Decimal

            amount = Decimal(compact)
            multiplier = {"tỷ": Decimal("1000000000"), "triệu": Decimal("1000000"), "nghìn": Decimal("1000")}.get(suffix, Decimal("1"))
            number = str((amount * multiplier).quantize(Decimal("0.01"))).rstrip("0").rstrip(".")
        except Exception:
            continue
        key = (number, "vnd")
        if key not in seen:
            seen.add(key)
            values.append(key)
    return values[:24]


@router.post(
    "/calculator/bhyt/draft",
    response_model=CalculatorDraftResponse,
    summary="Prepare calculator inputs from source-backed evidence",
)
async def draft_bhyt_calculation(
    request: CalculatorDraftRequest,
    _user: dict = Depends(get_current_user),
) -> CalculatorDraftResponse:
    """Retrieve evidence and explicit values for the calculator UI.

    The endpoint deliberately returns suggestions, not an answer: legal
    applicability remains a user/reviewer choice and the Decimal endpoint is
    still the only place that computes money.
    """
    try:
        bundle = await get_runtime().retrieve_bundle(request.question)
    except Exception:
        logger.warning("Calculator draft retrieval failed", exc_info=True)
        return CalculatorDraftResponse(
            question=request.question,
            message="Chưa lấy được nguồn pháp lý. Bạn có thể thử lại hoặc nhập số liệu đã xác minh.",
        )
    evidence: list[CalculatorDraftEvidence] = []
    values: list[CalculatorDraftValue] = []
    for index, item in enumerate(bundle.evidence[:8]):
        quote = " ".join(str(item.content or "").split())[:1200]
        if not quote:
            continue
        evidence.append(
            CalculatorDraftEvidence(
                title=str(item.title or ""),
                section_title=str(item.section_title or ""),
                quote=quote,
                source_url=str(item.source_url or ""),
            )
        )
        for value, unit in _draft_number_values(quote):
            values.append(CalculatorDraftValue(value=value, unit=unit, evidence_index=len(evidence) - 1))
    return CalculatorDraftResponse(
        question=request.question,
        evidence=evidence,
        values=values,
        message=(
            "Đã tìm thấy giá trị được nêu rõ trong nguồn. Hãy chọn giá trị phù hợp cho từng kịch bản; hệ thống không tự đoán mức hưởng."
            if evidence
            else "Chưa tìm thấy đoạn nguồn phù hợp để gợi ý số liệu."
        ),
    )


@router.get(
    "/legal/timeline",
    response_model=LegalTimelineResponse,
    summary="Inspect a release-scoped legal document relationship timeline",
)
async def legal_timeline(
    document_number: str = Query(..., min_length=1, max_length=80),
    as_of: date | None = Query(default=None),
    _user: dict = Depends(get_current_user),
) -> LegalTimelineResponse:
    """Hydrate a bounded graph walk back to public canonical metadata."""
    if not get_settings().feature_timeline_enabled:
        raise HTTPException(status_code=404, detail="Timeline unavailable")
    selected_date = as_of or date.today()
    async with session_scope() as session:
        repository = GraphRepository(session)
        seed = await repository.public_document_metadata(document_number)
        if not seed:
            raise HTTPException(status_code=404, detail="Document not found")
    seed_id = str(seed.get("id") or "")
    dataset_id = str(seed.get("dataset_id") or "")
    relations = []
    degraded = False
    if get_settings().feature_graph_enabled:
        try:
            relations = await asyncio.wait_for(
                get_runtime().document_relations(
                    [seed_id], dataset_id=dataset_id, hops=2, limit=40
                ),
                timeout=min(5.0, float(get_settings().retrieval_timeout_seconds)),
            )
        except Exception:
            logger.warning("Legal timeline graph degraded", exc_info=True)
            degraded = True
    related_ids = list(dict.fromkeys(
        identifier
        for relation in relations
        for identifier in (relation.source_id, relation.target_id)
        if identifier
    ))
    async with session_scope() as session:
        repository = GraphRepository(session)
        hydrated = await repository.public_document_metadata_by_ids(
            [seed_id, *related_ids], dataset_id=dataset_id
        )
    timeline = assemble_public_timeline(
        seed_document_id=seed_id,
        documents=hydrated,
        relations=relations,
        as_of=selected_date,
        degraded=degraded,
    )
    return LegalTimelineResponse(**timeline)


@router.post(
    "/eligibility/checklist",
    response_model=EligibilityChecklistResponse,
    summary="Ask only for missing user facts that can change a BHYT outcome",
)
async def eligibility_checklist(
    request: EligibilityChecklistRequest,
    user: dict = Depends(get_current_user),
) -> EligibilityChecklistResponse:
    """Build a deterministic checklist; current law is retrieved afterwards."""
    if not get_settings().feature_eligibility_enabled:
        raise HTTPException(status_code=404, detail="Eligibility checklist unavailable")
    try:
        result = build_eligibility_checklist(request.topic, request.facts)
    except ChecklistInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    persisted = False
    if request.conversation_id:
        try:
            release_id = await _context_release_id()
            owner_uid = str(user.get("uid") or "")
            persisted = await get_conversation_store().upsert_facts(
                owner_uid=owner_uid,
                conversation_id=request.conversation_id,
                facts=request.facts,
                dataset_id=release_id,
            )
            if persisted:
                await get_conversation_cache().invalidate(
                    owner_uid=owner_uid,
                    conversation_id=request.conversation_id,
                    release_id=release_id,
                    prompt_version=_context_prompt_version(),
                )
        except ConversationStoreError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return EligibilityChecklistResponse(
        **result,
        conversation_id=request.conversation_id,
        facts_persisted=persisted,
    )


@router.get(
    "/documents/{document_number:path}/html",
    response_class=HTMLResponse,
    summary="Render sanitized canonical legal HTML",
)
async def document_html(
    document_number: str,
    _user: dict = Depends(get_current_user),
) -> HTMLResponse:
    """Expose public-signature HTML only after server-side sanitization."""
    if not get_settings().feature_viewer_enabled:
        raise HTTPException(status_code=404, detail="Document viewer unavailable")
    # Signatures are identifiers, never paths.  Reject every parent segment
    # (including a leading ``../`` that would evade a substring-only check)
    # before touching the database.
    if (
        len(document_number) > 80
        or "\\" in document_number
        or any(segment == ".." for segment in document_number.split("/"))
    ):
        raise HTTPException(status_code=404, detail="Document not found")
    async with session_scope() as session:
        document = await GraphRepository(session).public_document_html(document_number)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    raw_html = str(document.get("raw_html") or "")
    expected_hash = str(document.get("raw_html_sha256") or "").strip().casefold()
    if expected_hash and hashlib.sha256(raw_html.encode("utf-8")).hexdigest() != expected_hash:
        raise HTTPException(status_code=503, detail="Document integrity check failed")
    body = sanitize_document_html(raw_html)
    title = str(document.get("title") or document_number)
    # The viewer is deliberately a fragment.  The frontend supplies the
    # surrounding chrome and its CSP; no active source content is returned.
    html = f'<article data-document="{escape(document_number, quote=True)}" data-title="{escape(title, quote=True)}">{body}</article>'
    return HTMLResponse(
        html,
        headers={
            "Content-Security-Policy": "default-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "public, max-age=300, stale-while-revalidate=60",
        },
    )


def _langgraph_provider():
    return get_agent()


def _answer_use_case() -> AnswerLegalQuestion:
    return AnswerLegalQuestion(LangGraphAgentAdapter(_langgraph_provider))


async def run_agent(
    message: str,
    *,
    feature: str = "chat",
    request_id: str | None = None,
    conversation_id: str | None = None,
    turn_id: str | None = None,
    owner_uid: str | None = None,
) -> dict:
    try:
        return await _invoke_agent(
            message,
            feature=feature,
            request_id=request_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            owner_uid=owner_uid,
        )
    except GraphRagUnavailableError as exc:
        logger.exception("GraphRAG retrieval failure")
        raise HTTPException(status_code=503, detail="GraphRAG service unavailable") from exc
    except ChatProviderError as exc:
        logger.exception("Chat provider failure")
        raise HTTPException(status_code=502, detail="Chat provider unavailable") from exc
    except RuntimeError as exc:
        logger.exception("Agent runtime failure")
        raise HTTPException(status_code=503, detail="Agent service unavailable") from exc
    except Exception as exc:
        logger.exception("Agent request failure")
        raise HTTPException(status_code=500, detail="Internal agent error") from exc


async def _invoke_agent(
    message: str,
    *,
    feature: str,
    request_id: str | None,
    conversation_id: str | None = None,
    turn_id: str | None = None,
    owner_uid: str | None = None,
) -> dict:
    resolved_message = message
    if owner_uid and conversation_id:
        try:
            turns = await _recent_turns_for_request(
                owner_uid=owner_uid, conversation_id=conversation_id
            )
            resolved_message = resolve_conversational_query(message, turns)
            resolved_message = apply_structured_user_facts(resolved_message, turns)
        except Exception:
            logger.exception("Conversation context resolution failed", extra={"request_id": request_id})
    if not tracing_enabled():
        return await _answer_use_case().execute(resolved_message)

    configure_langfuse()
    from langfuse import get_client, propagate_attributes

    trace_name = "chat-response" if feature == "chat" else "analyze-request"
    settings = get_settings()
    langfuse = get_client()
    with langfuse.start_as_current_observation(as_type="span", name=trace_name) as span:
        span.update(input={"message": resolved_message})
        with propagate_attributes(
            session_id=conversation_id or request_id,
            tags=[feature, "graphrag"],
            environment=settings.app_env,
            metadata={
                "request_id": request_id or "",
                "feature": feature,
                "conversation_id": conversation_id or "",
                "turn_id": turn_id or "",
                "model_version": settings.model_name,
                "prompt_registry": settings.prompt_registry_name,
            },
        ):
            result = await _answer_use_case().execute(resolved_message)
        response = result.get("response", "")
        citations = result.get("citations") or []
        span.update(
            output={
                "response": response if isinstance(response, str) else "",
                "citation_count": len(citations),
                "claim_count": len(result.get("claims") or []),
            },
            metadata={"stage_metrics": _trace_stage_metrics(result)},
        )
        return result


def _sse_event(event_type: str, payload: dict, *, event_id: int | None = None) -> str:
    identifier = f"id: {event_id}\n" if event_id is not None else ""
    return f"{identifier}event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _persist_chat_turn(
    *,
    owner_uid: str,
    request: ChatRequest,
    response: str,
    citations: list[dict],
    claims: list[dict],
    request_id: str,
) -> None:
    if not request.conversation_id or not request.turn_id:
        return
    try:
        await get_conversation_store().append_turn(
            owner_uid=owner_uid,
            conversation_id=request.conversation_id,
            turn_id=request.turn_id,
            user_message=request.message,
            assistant_response=response,
            citations=citations,
            claims=claims,
            anchors=build_conversation_anchors(citations),
            request_id=request_id,
        )
        await get_conversation_cache().invalidate(
            owner_uid=owner_uid, conversation_id=request.conversation_id,
            release_id=await _context_release_id(),
            prompt_version=_context_prompt_version(),
        )
    except ConversationStoreError as exc:
        logger.warning("Conversation turn rejected", extra={"reason": str(exc), "request_id": request_id})
    except Exception:
        logger.exception("Conversation turn persistence failed", extra={"request_id": request_id})


async def _stream_agent(
    message: str,
    *,
    request_id: str | None,
    conversation_id: str = "",
    turn_id: str = "",
    owner_uid: str = "",
    http_request: Request,
    idempotency_key: str = "",
    idempotency_endpoint: str = "/api/v1/chat/stream",
) -> AsyncIterator[str]:
    """Stream safe lifecycle/final events from the verified LangGraph run.

    Raw model tokens are intentionally not forwarded: the guardrail and
    citation builder must finish first, otherwise an unsupported token could
    reach the browser before verification. Clients still get immediate stage
    progress and can cancel the request without changing the JSON endpoint.
    """
    resolved_message = message
    if owner_uid and conversation_id:
        try:
            turns = await _recent_turns_for_request(
                owner_uid=owner_uid, conversation_id=conversation_id
            )
            resolved_message = resolve_conversational_query(message, turns)
            resolved_message = apply_structured_user_facts(resolved_message, turns)
        except Exception:
            logger.exception("Conversation stream context resolution failed", extra={"request_id": request_id})
    # Stable IDs are passed through to persistence; stream tracing remains a
    # separate contract until provider token callbacks are wired.
    final: dict | None = None
    event_id = 0

    def emit(event_type: str, payload: dict) -> str:
        nonlocal event_id
        event_id += 1
        return _sse_event(event_type, payload, event_id=event_id)

    stream_use_case = StreamLegalQuestion(LangGraphAgentAdapter(_langgraph_provider))
    try:
        stream_started = time.perf_counter()
        yield emit("status", {"stage": "started"})
        metrics.observe(
            "stream_first_event_seconds",
            time.perf_counter() - stream_started,
            outcome="status",
        )
        async with trace_span(
            "chat-stream",
            as_type="span",
            input={"message": resolved_message},
            metadata={
                "request_id": request_id or "",
                "conversation_id": conversation_id,
                "turn_id": turn_id,
                "feature": "chat-stream",
                "model_version": get_settings().model_name,
                "prompt_registry": get_settings().prompt_registry_name,
            },
        ) as stream_span:
            event_stream = stream_use_case.execute(resolved_message)
            try:
                async for chain_event in event_stream:
                    if await http_request.is_disconnected():
                        return
                    event_name = str(chain_event.get("name") or "")
                    event_type = str(chain_event.get("event") or "")
                    if event_type == "on_chain_start" and event_name in {
                        "retrieve_vectors", "assemble_context", "verify_evidence", "generate", "guardrail"
                    }:
                        yield emit("status", {"stage": event_name})
                    if event_type == "on_chain_end" and event_name == "guardrail":
                        output = chain_event.get("data", {}).get("output")
                        if isinstance(output, dict):
                            final = output
            finally:
                close_stream = getattr(event_stream, "aclose", None)
                if close_stream is not None:
                    await close_stream()
            if stream_span is not None:
                stream_span.update(
                    output={
                        "verified": bool(final),
                        "citation_count": len((final or {}).get("citations") or []),
                    },
                    metadata={"stage_metrics": _trace_stage_metrics(final or {})},
                )
        if not final:
            raise RuntimeError("Agent stream ended without a verified final event")
        response = final.get("response")
        if not isinstance(response, str) or not response.strip():
            raise RuntimeError("Agent returned an empty response")
        browser_citations = public_citations(final.get("citations") or [])
        final_payload = {
            "response": response.strip(),
            "citations": [citation.model_dump() for citation in browser_citations],
            "request_id": request_id or "",
            "conversation_id": conversation_id,
            "turn_id": turn_id,
        }
        route = _public_route(final)
        if route:
            final_payload["route"] = route
        yield emit("final", final_payload)
        if idempotency_key:
            await get_idempotency_store().complete(
                owner_uid=owner_uid,
                endpoint=idempotency_endpoint,
                key=idempotency_key,
                request_id=request_id or "",
                response=final_payload,
            )
        await _persist_chat_turn(
            owner_uid=owner_uid,
            request=ChatRequest(message=message, conversation_id=conversation_id, turn_id=turn_id),
            response=response.strip(),
            citations=final.get("citations") or [],
            claims=final.get("claims") or [],
            request_id=request_id or "",
        )
        yield emit("done", {"ok": True})
    except GraphRagUnavailableError as exc:
        # Preserve a typed terminal error so the browser can distinguish a
        # retryable dependency/deadline failure from an unexpected stream bug.
        # Release an idempotency lease on failure instead of stranding it.
        logger.exception("Agent stream retrieval failure")
        if idempotency_key:
            await get_idempotency_store().abort(
                owner_uid=owner_uid,
                endpoint=idempotency_endpoint,
                key=idempotency_key,
            )
        timed_out = "deadline" in str(exc).casefold()
        yield emit(
            "error",
            {
                "code": "retrieval_timeout" if timed_out else "retrieval_unavailable",
                "message": "Retrieval deadline exceeded" if timed_out else "GraphRAG service unavailable",
            },
        )
    except Exception as exc:
        logger.exception("Agent stream failure")
        if idempotency_key:
            await get_idempotency_store().abort(
                owner_uid=owner_uid,
                endpoint=idempotency_endpoint,
                key=idempotency_key,
            )
        del exc
        yield emit("error", {"code": "stream_unavailable", "message": "Chat stream unavailable"})


@router.post(
    "/chat",
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
    summary="Chat with the BHYT legal assistant",
    description=(
        "Send a BHYT or hospital-fee question. The assistant answers only from "
        "the active, verified legal corpus and returns public source citations."
    ),
    responses={
        200: {"description": "Source-backed answer and public citations."},
        422: {"description": "Invalid request payload."},
        502: {"description": "OpenAI chat provider unavailable."},
        503: {"description": "Required retrieval dependency unavailable."},
        500: {"description": "Unexpected internal error."},
    },
)
async def chat(
    request: ChatRequest,
    http_request: Request,
    user: dict = Depends(get_current_user),
) -> ChatResponse:
    owner_uid = str(user.get("uid") or "")
    endpoint = "/api/v1/chat"
    key, decision = await _begin_idempotency(
        http_request,
        owner_uid=owner_uid,
        endpoint=endpoint,
        payload=request.model_dump(mode="json"),
    )
    if decision.state == "replay" and decision.response:
        return ChatResponse(**decision.response)
    try:
        result = await run_agent(
            request.message,
            feature="chat",
            request_id=_request_id(http_request),
            conversation_id=request.conversation_id,
            turn_id=request.turn_id,
            owner_uid=owner_uid,
        )
        response = result.get("response")
        if not isinstance(response, str) or not response.strip():
            logger.error("Agent returned empty response")
            raise HTTPException(status_code=502, detail="Chat provider returned an empty response")

        internal_citations = [
            citation for citation in result.get("citations", []) if isinstance(citation, dict)
        ]
        internal_claims = [
            claim for claim in result.get("claims", []) if isinstance(claim, dict)
        ]
        citations = public_citations(internal_citations)
        await _persist_chat_turn(
            owner_uid=owner_uid,
            request=request,
            response=response.strip(),
            citations=internal_citations,
            claims=internal_claims,
            request_id=_request_id(http_request),
        )
        public_response = ChatResponse(
            response=response.strip(),
            citations=citations,
            request_id=_request_id(http_request),
            conversation_id=request.conversation_id,
            turn_id=request.turn_id,
        )
        if key:
            await get_idempotency_store().complete(
                owner_uid=owner_uid,
                endpoint=endpoint,
                key=key,
                request_id=public_response.request_id,
                response=public_response.model_dump(mode="json"),
            )
        return public_response
    except Exception:
        if key:
            await get_idempotency_store().abort(owner_uid=owner_uid, endpoint=endpoint, key=key)
        raise


@router.post(
    "/chat/stream",
    status_code=status.HTTP_200_OK,
    summary="Stream verified chat lifecycle and final answer",
    responses={200: {"description": "Server-sent events; final is emitted only after guardrails."}},
)
async def chat_stream(
    request: ChatRequest,
    http_request: Request,
    user: dict = Depends(get_current_user),
) -> StreamingResponse:
    owner_uid = str(user.get("uid") or "")
    endpoint = "/api/v1/chat/stream"
    key, decision = await _begin_idempotency(
        http_request,
        owner_uid=owner_uid,
        endpoint=endpoint,
        payload=request.model_dump(mode="json"),
    )
    if decision.state == "replay" and decision.response:
        return StreamingResponse(
            _replay_stream(decision.response),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    return StreamingResponse(
        _stream_agent(
            request.message,
            request_id=getattr(http_request.state, "request_id", None),
            conversation_id=request.conversation_id,
            turn_id=request.turn_id,
            owner_uid=owner_uid,
            http_request=http_request,
            idempotency_key=key,
            idempotency_endpoint=endpoint,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    status_code=status.HTTP_200_OK,
    summary="Analyze user input",
    description="Compatibility endpoint for non-conversational analysis.",
)
async def analyze(
    request: AnalyzeRequest,
    http_request: Request,
    _user: dict = Depends(get_current_user),
) -> AnalyzeResponse:
    result = await run_agent(
        request.message,
        feature="analyze",
        request_id=getattr(http_request.state, "request_id", None),
    )
    return AnalyzeResponse(analysis=result.get("response", ""))


@router.get(
    "/status",
    response_model=AgentStatusResponse,
    summary="Get agent status",
    description="Check whether the LangGraph GraphRAG agent is available.",
)
async def agent_status() -> AgentStatusResponse:
    return AgentStatusResponse(status="ready", agent="LangGraph GraphRAG Agent v1.0")


@router.post(
    "/research/jobs",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a bounded deep/global research request",
)
async def submit_research_job(
    request: ChatRequest,
    _http_request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    """Queue only routes that may exceed the interactive latency budget."""
    route = build_route_plan(request.message, settings=get_settings())
    if route.route not in {"deep", "global"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only deep or global research requests use the async job endpoint",
        )
    owner_uid = str(user.get("uid") or "").strip()
    if not owner_uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User identity is required")
    release_id = await _context_release_id()
    if not release_id:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Active release unavailable")
    try:
        queue = _get_research_queue()
        if isinstance(queue, RedisResearchJobQueue):
            job = await queue.submit(
                owner_uid=owner_uid,
                conversation_id=request.conversation_id or "default",
                release_id=release_id,
                query=request.message,
            )
        else:
            from src.research_worker import execute_research

            job = await queue.submit(
                owner_uid=owner_uid,
                conversation_id=request.conversation_id or "default",
                release_id=release_id,
                query=request.message,
                executor=execute_research,
            )
    except ResearchQueueFullError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Research queue is full") from exc
    except (RuntimeError, ValueError) as exc:
        logger.exception("Research queue unavailable")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Research queue unavailable") from exc
    return job.public_status()


@router.get("/research/jobs/{job_id}", summary="Read an owner-isolated research job")
async def get_research_job(
    job_id: str,
    conversation_id: str = Query(default="default", max_length=128),
    user: dict = Depends(get_current_user),
) -> dict:
    owner_uid = str(user.get("uid") or "").strip()
    job = await _get_research_queue().get(
        owner_uid=owner_uid,
        conversation_id=conversation_id or "default",
        job_id=job_id,
    )
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Research job not found")
    return job.public_status()


@router.delete("/research/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Cancel a research job")
async def cancel_research_job(
    job_id: str,
    conversation_id: str = Query(default="default", max_length=128),
    user: dict = Depends(get_current_user),
) -> None:
    owner_uid = str(user.get("uid") or "").strip()
    cancelled = await _get_research_queue().cancel(
        owner_uid=owner_uid,
        conversation_id=conversation_id or "default",
        job_id=job_id,
    )
    if not cancelled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Research job not found")
