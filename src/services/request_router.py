"""Input safety and model-assisted task routing.

The router is deliberately not an answer engine.  It returns a small typed
plan that is clamped by the deterministic route policy before retrieval.  If
the optional classifier is unavailable, the existing route planner remains a
safe and fully functional fallback.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.domain.route_plan import Route, RoutePlan, build_route_plan
from src.integrations.langfuse import llm_invoke_config
from src.services.llm import get_router_llm


class RouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: Literal["policy", "exact", "table", "topical", "temporal", "relational", "global", "deep"]
    risk: Literal["low", "medium", "high"]
    sub_tasks: list[str] = Field(default_factory=list, max_length=3)
    needs_table: bool = False
    needs_calculator: bool = False
    needs_graph: bool = False
    needs_current_law: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


@dataclass(frozen=True)
class InputGuardrailResult:
    allowed: bool
    query: str
    reason: str = ""


_INJECTION_MARKERS = (
    "ignore previous instructions",
    "reveal system prompt",
    "show system prompt",
    "bỏ qua hướng dẫn hệ thống",
    "bỏ qua mọi hướng dẫn",
    "hiện system prompt",
    "in prompt hệ thống",
    "api key",
    "private key",
    "secret key",
)
_INTERNAL_TOKEN = re.compile(r"\b(?:dataset|chunk|trace|evidence|claim)_?id\s*[:=]", re.I)


def input_guardrail(query: str) -> InputGuardrailResult:
    normalized = " ".join(query.split())
    lowered = normalized.casefold()
    if not normalized:
        return InputGuardrailResult(False, "", "empty_query")
    if any(marker in lowered for marker in _INJECTION_MARKERS) or _INTERNAL_TOKEN.search(normalized):
        return InputGuardrailResult(False, normalized, "prompt_injection_or_internal_request")
    return InputGuardrailResult(True, normalized)


def _baseline_decision(plan: RoutePlan) -> RouteDecision:
    return RouteDecision(
        route=plan.route,
        risk=plan.risk,
        needs_table=plan.route == "table",
        needs_calculator=plan.route == "table" and any(token in plan.required_facts for token in ("conditions", "authority")),
        needs_graph="neo4j" in plan.providers,
        needs_current_law=plan.risk == "high",
        confidence=0.5,
    )


def _clamp_decision(decision: RouteDecision, baseline: RouteDecision) -> RouteDecision:
    # Model output may refine the task, but cannot bypass deterministic safety
    # boundaries or activate providers outside the route contract.
    route: Route = decision.route
    if baseline.route == "policy":
        route = "policy"
    if baseline.route == "table" or decision.needs_table:
        route = "table"
    elif baseline.route == "exact":
        route = "exact"
    graph_allowed = route in {"temporal", "relational"}
    return decision.model_copy(
        update={
            "route": route,
            "risk": "high" if baseline.risk == "high" else decision.risk,
            "needs_graph": bool(decision.needs_graph and graph_allowed),
            "sub_tasks": [" ".join(item.split())[:240] for item in decision.sub_tasks[:3] if item.strip()],
        }
    )


_ROUTER_INSTRUCTION = """Phân loại nhiệm vụ cho trợ lý pháp luật BHYT. Chỉ trả JSON đúng schema.
Bạn chỉ được chọn route và cờ tác vụ; không trả lời câu hỏi, không thêm con số,
tên văn bản hoặc dữ kiện pháp lý. Giữ nguyên ý người dùng. route=table khi cần
con số/bảng/tính tiền; temporal khi hỏi hiệu lực/thời điểm; relational khi so
sánh/thay thế/liên hệ văn bản; exact khi có số hiệu; topical cho câu hỏi chung.
needs_graph chỉ bật cho temporal/relational. needs_calculator chỉ bật khi cần
phép tính từ giá trị người dùng hoặc giá trị đã truy hồi."""


async def classify_request(query: str, *, settings) -> tuple[RouteDecision, str]:
    baseline_plan = build_route_plan(query, settings=settings)
    baseline = _baseline_decision(baseline_plan)
    if not getattr(settings, "model_router_enabled", True):
        return baseline, "deterministic_disabled"
    # Obvious low-cost paths should not pay for a classifier round trip.
    if baseline.route in {"policy", "exact"} or len(query.split()) < 5:
        return baseline, "deterministic_shape"
    try:
        structured = get_router_llm().with_structured_output(RouteDecision, method="json_schema")
        result = await asyncio.wait_for(
            structured.ainvoke(
                [("system", _ROUTER_INSTRUCTION), ("human", query)],
                config=llm_invoke_config(),
            ),
            timeout=float(settings.model_router_timeout_seconds),
        )
        decision = result if isinstance(result, RouteDecision) else RouteDecision.model_validate(result)
        return _clamp_decision(decision, baseline), "model"
    except Exception:
        return baseline, "deterministic_fallback"


__all__ = ["InputGuardrailResult", "RouteDecision", "classify_request", "input_guardrail"]
