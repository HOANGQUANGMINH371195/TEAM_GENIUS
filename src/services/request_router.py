"""Input safety and model-assisted task routing.

The router is deliberately not an answer engine.  It returns a small typed
plan that is clamped by the deterministic route policy before retrieval.  If
the optional classifier is unavailable, the existing route planner remains a
safe and fully functional fallback.
"""

from __future__ import annotations

import asyncio
import re
import time
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
    direct_response: str | None = Field(default=None, max_length=240)


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
_ROUTER_CACHE: dict[tuple[str, str], tuple[RouteDecision, float]] = {}
_ROUTER_CACHE_TTL_SECONDS = 300.0
_ROUTER_CACHE_MAX_ENTRIES = 512


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
    # Policy is the only route that can be forced by the deterministic layer:
    # a legal request must never be answered by a short social response. All
    # other intent choices belong to the model; ``needs_table`` is an explicit
    # typed capability flag, not a lexical override.
    if baseline.route == "policy":
        route = "policy"
    elif route == "policy" and not decision.direct_response:
        route = baseline.route
    if decision.needs_table:
        route = "table"
    graph_allowed = route in {"temporal", "relational"}
    return decision.model_copy(
        update={
            "route": route,
            "risk": "high" if baseline.risk == "high" else decision.risk,
            "needs_graph": bool(decision.needs_graph and graph_allowed),
            "sub_tasks": [" ".join(item.split())[:240] for item in decision.sub_tasks[:3] if item.strip()],
            "direct_response": (
                " ".join((decision.direct_response or "").split())[:240]
                if route == "policy" and decision.direct_response
                else None
            ),
        }
    )


_ROUTER_INSTRUCTION = """Phân loại nhiệm vụ cho trợ lý pháp luật BHYT. Chỉ trả JSON đúng schema.
Bạn chỉ được chọn route và cờ tác vụ; không trả lời câu hỏi pháp lý, không thêm con số,
tên văn bản hoặc dữ kiện pháp lý. Giữ nguyên ý người dùng. Ưu tiên route theo các quy
tắc sau khi câu hỏi có nhiều tín hiệu: policy cho greeting/cảm ơn/ngoài phạm vi;
exact khi yêu cầu tra một văn bản/điều khoản cụ thể (trừ khi trọng tâm là hiệu lực
hoặc thay thế); temporal khi hỏi hiệu lực, thời hạn, dòng thời gian, thay đổi theo
năm; relational khi so sánh, đối chiếu hai kịch bản/phương án
(kể cả "đối chiếu hai kịch bản"), phương án nào có lợi hơn, thay thế hoặc mối liên hệ; global khi yêu cầu tổng
quan/toàn quốc/toàn bộ phạm vi; deep khi yêu cầu phân tích toàn diện, nhiều khía
cạnh; table khi cần con số, tỷ lệ, mức đóng/hỗ trợ, bảng hoặc phép tính tiền cụ
thể (ví dụ hỏi học sinh đóng bao nhiêu tiền; không dùng table chỉ vì có từ
"tính" trong câu hỏi); topical cho câu hỏi BHYT chung, quyền lợi liên tục,
checklist, hồ sơ hoặc điều kiện còn lại.
needs_graph chỉ bật cho temporal/relational. needs_calculator chỉ bật khi cần
phép tính từ giá trị người dùng hoặc giá trị đã truy hồi. Với greeting, cảm ơn
hoặc câu hỏi ngoài phạm vi BHYT, route=policy và điền direct_response là một câu
tiếng Việt lịch sự, tối đa 25 từ; các route khác phải để direct_response=null."""


async def classify_request(query: str, *, settings) -> tuple[RouteDecision, str]:
    baseline_plan = build_route_plan(query, settings=settings)
    baseline = _baseline_decision(baseline_plan)
    if not getattr(settings, "model_router_enabled", True):
        return baseline, "deterministic_disabled"
    cache_key = (
        str(getattr(settings, "model_router_model_name", "") or getattr(settings, "model_name", "")),
        " ".join(query.casefold().split()),
    )
    cached = _ROUTER_CACHE.get(cache_key)
    if cached and time.monotonic() - cached[1] < _ROUTER_CACHE_TTL_SECONDS:
        return cached[0], "model_cache"
    # Every allowed request is classified by the model. The deterministic
    # planner remains a policy boundary/fallback, not the normal router; this
    # keeps paraphrases, compound questions and new features on one contract.
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
        decision = _clamp_decision(decision, baseline)
        if len(_ROUTER_CACHE) >= _ROUTER_CACHE_MAX_ENTRIES:
            oldest = min(_ROUTER_CACHE, key=lambda item: _ROUTER_CACHE[item][1])
            _ROUTER_CACHE.pop(oldest, None)
        _ROUTER_CACHE[cache_key] = (decision, time.monotonic())
        return decision, "model"
    except Exception:
        return baseline, "deterministic_fallback"


__all__ = ["InputGuardrailResult", "RouteDecision", "classify_request", "input_guardrail"]
