"""Typed, deterministic query-routing contract.

The router is intentionally cheap and explainable.  It chooses a bounded
retrieval plan; it never decides a legal answer and it never adds domain facts
to a user's question.  Expensive providers are enabled only for routes that
can benefit from them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.services.retrieval import extract_document_numbers, retrieval_intent

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
    if extract_document_numbers(query) or intent == "lookup":
        route: Route = "exact"
    elif any(token in normalized for token in ("bao nhiêu tiền", "bao nhiêu %", "phần trăm", "tính", "chi phí")):
        route = "table"
    elif intent == "temporal":
        route = "temporal"
    elif intent == "relational":
        route = "relational"
    else:
        route = "topical"

    high_risk = any(
        token in normalized
        for token in (
            "được hưởng", "được chi trả", "thanh toán", "mức hưởng", "hiệu lực",
            "bãi bỏ", "thay thế", "không được hưởng", "có được",
        )
    )
    risk: Literal["low", "medium", "high"] = "high" if high_risk else "medium"
    providers: list[str] = ["postgres"]
    if route in {"topical", "temporal", "relational", "global", "deep"}:
        providers.append("qdrant")
    if route in {"temporal", "relational"} and getattr(settings, "feature_graph_enabled", True):
        providers.append("neo4j")
    required_facts = ("authority", "conditions", "exceptions", "effective_interval") if risk == "high" else ("authority",)
    return RoutePlan(
        route=route,
        risk=risk,
        required_facts=required_facts,
        providers=tuple(providers),
        retrieval_budget_ms=15_000 if route in {"temporal", "relational"} else 8_000,
        generation_budget_ms=int(float(settings.llm_timeout_seconds) * 1000),
        max_candidates=min(int(settings.retrieval_candidate_k), 30 if route != "exact" else 12),
        context_budget=min(int(settings.max_context_chars), 100_000),
        verifier_policy="strict" if risk == "high" else "standard",
    )
