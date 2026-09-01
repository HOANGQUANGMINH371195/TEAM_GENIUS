"""Typed, deterministic query-routing contract.

The router is intentionally cheap and explainable.  It chooses a bounded
retrieval plan; it never decides a legal answer and it never adds domain facts
to a user's question.  Expensive providers are enabled only for routes that
can benefit from them.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from src.services.retrieval import (
    extract_document_numbers,
    requires_evidence_verification,
    retrieval_intent,
)

Route = Literal[
    "policy",
    "exact",
    "table",
    "topical",
    "temporal",
    "relational",
    "global",
    "deep",
]


@dataclass(frozen=True)
class RoutePlan:
    """Budget and provider contract carried through one request."""

    route: Route
    risk: Literal["low", "medium", "high"]
    required_facts: tuple[str, ...]
    providers: tuple[str, ...]
    retrieval_budget_ms: int
    generation_budget_ms: int
    max_candidates: int
    context_budget: int
    verifier_policy: Literal["none", "standard", "strict"]

    def as_dict(self) -> dict[str, object]:
        return {
            "route": self.route,
            "risk": self.risk,
            "required_facts": list(self.required_facts),
            "providers": list(self.providers),
            "retrieval_budget_ms": self.retrieval_budget_ms,
            "generation_budget_ms": self.generation_budget_ms,
            "max_candidates": self.max_candidates,
            "context_budget": self.context_budget,
            "verifier_policy": self.verifier_policy,
        }


def apply_model_route(
    plan: RoutePlan,
    *,
    route: str,
    risk: str | None = None,
    needs_graph: bool = False,
    settings,
) -> RoutePlan:
    """Project a validated model route onto the bounded resource contract.

    The model owns intent selection.  This helper only translates the typed
    route enum into provider/budget permissions; it never inspects query words
    or creates legal facts.  ``build_route_plan`` remains the deterministic
    fallback when the router is unavailable.
    """
    allowed: set[str] = {
        "policy", "exact", "table", "topical", "temporal", "relational", "global", "deep"
    }
    if route not in allowed:
        return plan
    resolved_risk = "high" if plan.risk == "high" else (risk if risk in {"low", "medium", "high"} else plan.risk)
    providers = ["postgres"]
    if route in {"table", "topical", "temporal", "relational", "global", "deep"}:
        providers.append("qdrant")
    if route == "global" and getattr(settings, "feature_global_search_enabled", False):
        providers.append("community")
    if (
        getattr(settings, "feature_graph_enabled", True)
        and needs_graph
        and route in {"temporal", "relational"}
    ):
        providers.append("neo4j")
    return replace(
        plan,
        route=route,  # type: ignore[arg-type]
        risk=resolved_risk,  # type: ignore[arg-type]
        providers=tuple(providers),
        retrieval_budget_ms=(
            15_000 if route in {"temporal", "relational", "global", "deep"} or resolved_risk == "high" else 8_000
        ),
        max_candidates=min(
            int(getattr(settings, "retrieval_candidate_k", plan.max_candidates)),
            12 if route == "exact" else 30,
        ),
        verifier_policy="strict" if resolved_risk == "high" else "standard",
    )


def build_route_plan(query: str, *, settings) -> RoutePlan:
    """Build a bounded plan from query shape and configured ceilings.

    This is not a keyword-to-answer mapping.  Signals are generic query
    shapes (social, identifier, date, table/numeric and relationship intent)
    and the final evidence verifier remains authoritative.
    """
    normalized = " ".join(query.casefold().split())
    if not normalized or normalized in {"hi", "hello", "xin chào", "cảm ơn", "thanks"}:
        return RoutePlan(
            route="policy", risk="low", required_facts=(), providers=(),
            retrieval_budget_ms=100, generation_budget_ms=500,
            max_candidates=0, context_budget=0, verifier_policy="none",
        )

    intent = retrieval_intent(query) if getattr(settings, "feature_planner_enabled", True) else "thematic"
    # Global/deep are query-shape routes, not a closed list of legal topics.
    # They opt into a larger bounded evidence budget; the planner still has
    # to prove an evidence gap before it fans out.
    deep_shape = any(
        marker in normalized
        for marker in (
            "phân tích sâu", "phân tích toàn diện", "đánh giá toàn diện",
            "tổng hợp nhiều văn bản", "so sánh toàn diện", "bức tranh toàn cảnh",
        )
    )
    global_shape = any(
        marker in normalized
        for marker in (
            "tổng quan", "toàn bộ quy định", "các quy định liên quan",
            "so sánh các quy định", "quy định trên toàn quốc",
        )
    )
    numeric_shape = any(
        token in normalized
        for token in (
            "bao nhiêu", "phần trăm", "tỷ lệ", "mức đóng", "mức hỗ trợ",
            "giá trị", "số tiền", "tính", "chi phí",
        )
    )
    temporal_status_shape = any(
        token in normalized
        for token in ("hiệu lực", "còn hiệu lực", "hết hiệu lực", "từ ngày", "thay đổi theo năm")
    )
    if deep_shape:
        route = "deep"
    elif global_shape:
        route = "global"
    # An identifier does not make every request a plain lookup.  Relationship
    # and temporal qualifiers must retain their specialized retrieval path so
    # Neo4j can contribute bounded, typed edges before evidence is rehydrated
    # from the authoritative stores.
    elif intent == "temporal" and not (numeric_shape and not temporal_status_shape):
        route = "temporal"
    elif intent == "relational":
        route = "relational"
    elif extract_document_numbers(query) or intent == "lookup":
        route = "exact"
    elif numeric_shape:
        route = "table"
    else:
        route = "topical"

    high_risk = requires_evidence_verification(query) or any(
        token in normalized
        for token in (
            "được hưởng", "được chi trả", "thanh toán", "mức hưởng", "hiệu lực",
            "bãi bỏ", "thay thế", "không được hưởng", "có được",
            "bao nhiêu", "phần trăm", "tỷ lệ", "mức đóng", "mức hỗ trợ",
        )
    )
    risk: Literal["low", "medium", "high"] = "high" if high_risk else "medium"
    providers: list[str] = ["postgres"]
    # Numeric/percentage questions are table-shaped, but a corpus may not yet
    # have a reviewed typed fact for every provision.  Keep dense recall as a
    # bounded fallback instead of abstaining on an irrelevant lexical head.
    if route in {"table", "topical", "temporal", "relational", "global", "deep"}:
        providers.append("qdrant")
    if route == "global" and getattr(settings, "feature_global_search_enabled", False):
        providers.append("community")
    if (
        getattr(settings, "feature_graph_enabled", True)
        and (route == "relational" or (route == "temporal" and extract_document_numbers(query)))
    ):
        providers.append("neo4j")
    required_facts = ("authority", "conditions", "exceptions", "effective_interval") if risk == "high" else ("authority",)
    return RoutePlan(
        route=route,
        risk=risk,
        required_facts=required_facts,
        providers=tuple(providers),
        # High-risk entitlement/payment questions need the document-bounded
        # rescue and currentness checks before a safe synthesis.  Giving that
        # bounded cascade the same 15s ceiling as temporal/relational routes
        # avoids an empty lexical fallback when a managed Postgres connection
        # is cold; low-risk topical lookups retain the 8s fast path.
        retrieval_budget_ms=(
            15_000
            if route in {"temporal", "relational", "global", "deep"} or risk == "high"
            else 8_000
        ),
        generation_budget_ms=int(float(settings.llm_timeout_seconds) * 1000),
        max_candidates=min(int(settings.retrieval_candidate_k), 30 if route != "exact" else 12),
        context_budget=min(int(settings.max_context_chars), 100_000),
        verifier_policy="strict" if risk == "high" else "standard",
    )
