from __future__ import annotations

import asyncio
import json
import logging
import math
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from src.integrations.langfuse import configure_langfuse, tracing_enabled
from src.models.schemas import (
    AdminObservabilityBreakdown,
    AdminObservabilityMetric,
    AdminObservabilityPoint,
    AdminObservabilityResponse,
    AdminObservabilitySummary,
)


class LangfuseDashboardInvalidResponseError(RuntimeError):
    """Raised when Langfuse returns an unusable metrics response."""


class LangfuseDashboardUnavailableError(RuntimeError):
    """Raised when core Langfuse dashboard data cannot be read."""


logger = logging.getLogger(__name__)

ObservabilityRange = Literal["today", "7d", "30d", "90d"]
_QueryMode = Literal["root", "generation", "error", "operations"]
_RANGE_DAYS: dict[ObservabilityRange, int] = {"today": 1, "7d": 7, "30d": 30, "90d": 90}
_QUERY_TIMEOUT_SECONDS = 8.0
_MAX_ROWS = 100
_ROOT_NAMES = ["chat-response", "chat-stream", "analyze-request"]
_ALLOWED_RANGE_NAMES = frozenset(_RANGE_DAYS)
_ALLOWED_MODES = frozenset({"root", "generation", "error", "operations"})
_DASHBOARD_CACHE_SECONDS = 30.0
_DASHBOARD_CACHE: dict[str, tuple[float, AdminObservabilityResponse]] = {}
_DASHBOARD_INFLIGHT: dict[str, asyncio.Task[AdminObservabilityResponse]] = {}


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _unavailable(range_name: ObservabilityRange, start: datetime, end: datetime, reason: str) -> AdminObservabilityResponse:
    return AdminObservabilityResponse(available=False, reason=reason, range=range_name, from_timestamp=start, to_timestamp=end, updated_at=_now_utc())


def _range_bounds(value: ObservabilityRange, now: datetime | None = None) -> tuple[datetime, datetime]:
    current = (now or _now_utc()).astimezone(UTC)
    start = current.replace(hour=0, minute=0, second=0, microsecond=0) if value == "today" else current - timedelta(days=_RANGE_DAYS[value])
    return start, current


def _metric_query(*, from_timestamp: datetime, to_timestamp: datetime, mode: _QueryMode = "root", time_granularity: str | None = "day") -> str:
    """Build fixed server-owned Langfuse Metrics API query."""
    if mode not in _ALLOWED_MODES:
        raise ValueError("Unsupported Langfuse dashboard query mode")
    filters: list[dict[str, Any]] = ([{"column": "type", "operator": "=", "value": "GENERATION", "type": "string"}] if mode == "generation" else [{"column": "name", "operator": "any of", "value": _ROOT_NAMES, "type": "stringOptions"}])
    if mode == "error":
        filters.append({"column": "level", "operator": "=", "value": "ERROR", "type": "string"})
    if mode == "generation":
        measures = [{"measure": "totalTokens", "aggregation": "sum"}, {"measure": "totalCost", "aggregation": "sum"}]
    elif mode == "error":
        measures = [{"measure": "count", "aggregation": "count"}]
    elif mode == "operations":
        measures = [{"measure": "count", "aggregation": "count"}, {"measure": "latency", "aggregation": "p95"}]
    else:
        measures = [{"measure": "count", "aggregation": "count"}, {"measure": "latency", "aggregation": "p95"}, {"measure": "totalTokens", "aggregation": "sum"}, {"measure": "totalCost", "aggregation": "sum"}]
    query: dict[str, Any] = {"view": "observations", "dimensions": [{"field": "name"}] if mode == "operations" else [], "filters": filters, "metrics": measures, "fromTimestamp": from_timestamp.isoformat(), "toTimestamp": to_timestamp.isoformat(), "config": {"row_limit": _MAX_ROWS}}
    if time_granularity is not None and mode != "operations":
        query["timeDimension"] = {"granularity": time_granularity}
    return json.dumps(query, separators=(",", ":"), sort_keys=True)


def _rows(response: Any) -> list[dict[str, Any]]:
    data = response.get("data") if isinstance(response, Mapping) else getattr(response, "data", None)
    if not isinstance(data, list) or any(not isinstance(row, Mapping) for row in data):
        raise LangfuseDashboardInvalidResponseError("Langfuse metrics response has invalid rows")
    return [dict(row) for row in data[:_MAX_ROWS]]


def _number(row: Mapping[str, Any], *names: str) -> float | None:
    for name in names:
        value = row.get(name)
        if isinstance(value, bool):
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed) and parsed >= 0:
            return parsed
    return None


def _value(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in row:
            return row[name]
    return None


def _timestamp(row: Mapping[str, Any], fallback: datetime) -> datetime:
    value = _value(row, "time_dimension", "startTimeHour", "startTimeDay", "timestampHour", "timestampDay", "startTime", "timestamp")
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if isinstance(value, str):
        try:
            if len(value) == 10:
                return datetime.fromisoformat(value).replace(tzinfo=UTC)
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return (parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)).astimezone(UTC)
        except ValueError:
            pass
    return fallback


def _metric_value(row: Mapping[str, Any], measure: str, aggregation: str) -> float | None:
    aliases = (
        f"{measure}_{aggregation}",
        f"{measure}{aggregation.title()}",
        f"{aggregation}_{measure}",
        f"{measure}_{aggregation.lower()}",
        measure,
    )
    direct = _number(row, *aliases)
    if direct is not None:
        return direct
    metric_aliases = row.get("metric")
    if isinstance(metric_aliases, Mapping):
        nested = metric_aliases.get(measure) or metric_aliases.get(f"{measure}_{aggregation}")
        if isinstance(nested, Mapping):
            return _number(nested, aggregation, "value")
        if nested is not None:
            return _number({"value": nested}, "value")
    metrics = row.get("metrics")
    if isinstance(metrics, Mapping):
        nested = metrics.get(measure) or metrics.get(f"{measure}_{aggregation}")
        if isinstance(nested, Mapping):
            return _number(nested, aggregation, "value")
        if nested is not None:
            return _number({"value": nested}, "value")
    return None


def _metric_total(rows: list[dict[str, Any]], measure: str) -> float | None:
    values = [_metric_value(row, measure, "sum") for row in rows]
    return sum(value for value in values if value is not None) if values and all(value is not None for value in values) else None


def _merge_buckets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(_value(row, "time_dimension", "startTimeHour", "startTimeDay", "timestampHour", "timestampDay", "startTime", "timestamp") or "")
        current = merged.setdefault(key, {})
        previous = dict(current)
        current.update(row)
        for field in row:
            if field == "count" or field.startswith("count_"):
                current[field] = (_number(previous, field) or 0) + (_number(row, field) or 0)
    return list(merged.values())[:_MAX_ROWS]


def _error_at(rows: list[dict[str, Any]], timestamp: datetime, granularity: str) -> int | None:
    for row in rows:
        candidate = _timestamp(row, timestamp)
        if (candidate.date() == timestamp.date() if granularity == "day" else candidate == timestamp):
            value = _metric_value(row, "count", "count")
            return int(value) if value is not None else None
    return None


def _build_dashboard(timeline: list[dict[str, Any]], summary: list[dict[str, Any]], usage: list[dict[str, Any]] | None, errors: list[dict[str, Any]] | None, operations: list[dict[str, Any]] | None, start: datetime, end: datetime, range_name: ObservabilityRange, granularity: str) -> AdminObservabilityResponse:
    summary_row = summary[0] if summary else (timeline[0] if timeline else {})
    request_count = _metric_value(summary_row, "count", "count")
    if request_count is None:
        values = [_metric_value(row, "count", "count") for row in timeline]
        request_count = sum(value for value in values if value is not None) if values and all(value is not None for value in values) else None
    error_values = [_metric_value(row, "count", "count") for row in (errors or [])]
    error_count = sum(value for value in error_values if value is not None) if errors is not None and error_values and all(value is not None for value in error_values) else None
    latency = _metric_value(summary_row, "latency", "p95")
    if latency is None:
        latency_values = [_metric_value(row, "latency", "p95") for row in timeline]
        latency = max(value for value in latency_values if value is not None) if latency_values and any(value is not None for value in latency_values) else None
    token_total = _metric_total(usage or [], "totalTokens") if usage is not None else None
    cost_total = _metric_total(usage or [], "totalCost") if usage is not None else None
    return AdminObservabilityResponse(
        available=bool(timeline or summary),
        reason="" if (timeline or summary) else "Không có dữ liệu Langfuse trong khoảng thời gian đã chọn.",
        range=range_name,
        from_timestamp=start,
        to_timestamp=end,
        updated_at=_now_utc(),
        summary=AdminObservabilitySummary(
            requests=AdminObservabilityMetric(value=request_count, observable=request_count is not None),
            error_rate=AdminObservabilityMetric(value=(error_count / request_count if error_count is not None and request_count else None), observable=error_count is not None and request_count is not None and request_count > 0),
            p95_latency_ms=AdminObservabilityMetric(value=latency, observable=latency is not None),
            total_tokens=AdminObservabilityMetric(value=token_total, observable=token_total is not None),
            total_cost_usd=AdminObservabilityMetric(value=cost_total, observable=cost_total is not None),
        ),
        series=[AdminObservabilityPoint(timestamp=(timestamp := _timestamp(row, start)), requests=(int(value) if (value := _metric_value(row, "count", "count")) is not None else None), errors=_error_at(errors or [], timestamp, granularity) if errors is not None else None, p95_latency_ms=_metric_value(row, "latency", "p95"), total_tokens=_metric_value(row, "totalTokens", "sum"), total_cost_usd=_metric_value(row, "totalCost", "sum")) for row in timeline],
        breakdowns=sorted([AdminObservabilityBreakdown(name=str(_value(row, "name", "observationName") or "Không xác định").strip() or "Không xác định", requests=(int(value) if (value := _metric_value(row, "count", "count")) is not None else None), p95_latency_ms=_metric_value(row, "latency", "p95")) for row in (operations or [])], key=lambda item: (-(item.requests or 0), item.name))[:10],
    )


def _aggregate(
    rows: list[dict[str, Any]],
    start: datetime,
    end: datetime,
    range_name: ObservabilityRange = "7d",
    *,
    summary_rows: list[dict[str, Any]] | None = None,
    usage_rows: list[dict[str, Any]] | None = None,
    error_rows: list[dict[str, Any]] | None = None,
    operations_rows: list[dict[str, Any]] | None = None,
    errors_observable: bool = False,
    granularity: str = "day",
) -> AdminObservabilityResponse:
    """Aggregate fixture rows using the same production response contract."""
    return _build_dashboard(
        rows,
        summary_rows if summary_rows is not None else [],
        usage_rows,
        error_rows if errors_observable or error_rows is not None else None,
        operations_rows,
        start,
        end,
        range_name,
        granularity,
    )


async def _metrics_call(client: Any, query: str) -> Any:
    return await client.async_api.metrics.metrics(query=query, request_options={"timeout_in_seconds": int(_QUERY_TIMEOUT_SECONDS), "max_retries": 0})


async def _fetch_uncached(range_name: ObservabilityRange) -> AdminObservabilityResponse:
    start, end = _range_bounds(range_name)
    if not tracing_enabled():
        return _unavailable(range_name, start, end, "Langfuse chưa được cấu hình.")
    try:
        configure_langfuse()
        from langfuse import get_client
        client = get_client()
        granularity = "hour" if range_name == "today" else "day"
        results = await asyncio.wait_for(asyncio.gather(
            _metrics_call(client, _metric_query(from_timestamp=start, to_timestamp=end, time_granularity=granularity)),
            _metrics_call(client, _metric_query(from_timestamp=start, to_timestamp=end, time_granularity=None)),
            _metrics_call(client, _metric_query(from_timestamp=start, to_timestamp=end, mode="generation", time_granularity=None)),
            _metrics_call(client, _metric_query(from_timestamp=start, to_timestamp=end, mode="error", time_granularity=granularity)),
            _metrics_call(client, _metric_query(from_timestamp=start, to_timestamp=end, mode="operations", time_granularity=None)),
            return_exceptions=True,
        ), timeout=_QUERY_TIMEOUT_SECONDS)
        timeline_result, summary_result, usage_result, error_result, operations_result = results
        if isinstance(timeline_result, Exception) or isinstance(summary_result, Exception):
            raise LangfuseDashboardUnavailableError("Langfuse core metrics unavailable")
        return _build_dashboard(_merge_buckets(_rows(timeline_result)), _rows(summary_result), None if isinstance(usage_result, Exception) else _rows(usage_result), None if isinstance(error_result, Exception) else _merge_buckets(_rows(error_result)), None if isinstance(operations_result, Exception) else _rows(operations_result), start, end, range_name, granularity)
    except LangfuseDashboardInvalidResponseError:
        logger.warning("Langfuse dashboard returned invalid response")
        return _unavailable(range_name, start, end, "Dữ liệu Langfuse không đúng định dạng.")
    except LangfuseDashboardUnavailableError:
        logger.warning("Langfuse dashboard core query unavailable")
        return _unavailable(range_name, start, end, "Langfuse hiện không phản hồi.")
    except Exception:
        logger.warning("Langfuse dashboard query unavailable", exc_info=True)
        return _unavailable(range_name, start, end, "Langfuse hiện không phản hồi.")


async def fetch_dashboard(range_name: ObservabilityRange) -> AdminObservabilityResponse:
    if range_name not in _ALLOWED_RANGE_NAMES:
        raise ValueError("Unsupported observability range")
    loop = asyncio.get_running_loop()
    cached = _DASHBOARD_CACHE.get(range_name)
    if cached and loop.time() - cached[0] < _DASHBOARD_CACHE_SECONDS:
        return cached[1]
    task = _DASHBOARD_INFLIGHT.get(range_name)
    if task is None or task.done():
        task = asyncio.create_task(_fetch_uncached(range_name))
        _DASHBOARD_INFLIGHT[range_name] = task
    try:
        result = await task
    finally:
        if _DASHBOARD_INFLIGHT.get(range_name) is task:
            _DASHBOARD_INFLIGHT.pop(range_name, None)
    if result.available:
        _DASHBOARD_CACHE[range_name] = (loop.time(), result)
    return result


def clear_dashboard_cache() -> None:
    _DASHBOARD_CACHE.clear()
    _DASHBOARD_INFLIGHT.clear()
