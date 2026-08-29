from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from src.services.langfuse_dashboard import (
    _aggregate,
    _metric_query,
    _range_bounds,
    fetch_dashboard,
)

START = datetime(2026, 8, 1, tzinfo=UTC)
END = datetime(2026, 8, 8, tzinfo=UTC)


def test_metric_query_is_server_owned_and_bounded():
    query = json.loads(_metric_query(from_timestamp=START, to_timestamp=END))
    assert query["view"] == "observations"
    assert query["config"] == {"row_limit": 100}
    assert query["timeDimension"] == {"granularity": "day"}
    assert query["filters"][0]["value"] == ["chat-response", "chat-stream", "analyze-request"]
    assert {metric["measure"] for metric in query["metrics"]} == {
        "count", "latency", "totalTokens", "totalCost"
    }
    serialized = json.dumps(query)
    assert all(secret not in serialized for secret in ("input", "output", "metadata", "userId", "sessionId"))


def test_metric_query_operation_mode_has_allowlisted_dimension():
    query = json.loads(_metric_query(
        from_timestamp=START,
        to_timestamp=END,
        mode="operations",
        time_granularity=None,
    ))
    assert query["dimensions"] == [{"field": "name"}]
    assert "timeDimension" not in query


def test_metric_query_generation_mode_is_usage_only():
    query = json.loads(_metric_query(
        from_timestamp=START,
        to_timestamp=END,
        mode="generation",
        time_granularity=None,
    ))
    assert query["filters"] == [{"column": "type", "operator": "=", "value": "GENERATION", "type": "string"}]
    assert {metric["measure"] for metric in query["metrics"]} == {"totalTokens", "totalCost"}


def test_metric_query_error_mode_uses_fixed_error_filter():
    query = json.loads(_metric_query(
        from_timestamp=START,
        to_timestamp=END,
        mode="error",
    ))
    assert query["filters"][1] == {"column": "level", "operator": "=", "value": "ERROR", "type": "string"}


def test_range_bounds_normalizes_to_utc():
    start, end = _range_bounds("today", datetime(2026, 8, 29, 12, 30, tzinfo=UTC))
    assert start == datetime(2026, 8, 29, tzinfo=UTC)
    assert end.tzinfo is UTC


def test_aggregate_normalizes_metric_api_aliases_and_units():
    result = _aggregate(
        [{"startTimeDay": "2026-08-01T00:00:00Z", "count_count": 4, "latency_p95": 750}],
        START,
        END,
        summary_rows=[{"count_count": 4, "latency_p95": 750}],
        usage_rows=[{"totalTokens_sum": 12, "totalCost_sum": 0.25}],
    )
    assert result.available is True
    assert result.summary.requests.value == 4
    assert result.summary.p95_latency_ms.value == 750
    assert result.summary.total_tokens.value == 12
    assert result.summary.total_cost_usd.value == 0.25


def test_aggregate_marks_missing_usage_and_errors_unknown():
    result = _aggregate(
        [{"startTimeDay": "2026-08-01T00:00:00Z", "count": 4, "latency": 750}],
        START,
        END,
    )
    assert result.summary.total_tokens.value is None
    assert result.summary.p95_latency_ms.value == 750
    assert result.summary.total_cost_usd.value is None
    assert result.summary.error_rate.value is None


def test_aggregate_preserves_explicit_zero():
    result = _aggregate([{"count": 0, "latency": 0}], START, END)
    assert result.summary.requests.value == 0
    assert result.summary.p95_latency_ms.value == 0


def test_rows_reject_non_mapping_rows():
    from src.services.langfuse_dashboard import LangfuseDashboardInvalidResponseError, _rows

    with pytest.raises(LangfuseDashboardInvalidResponseError):
        _rows({"data": [{"count": 1}, "bad"]})


def test_aggregate_rejects_negative_nonfinite_values():
    result = _aggregate([{"count": -1, "latency": float("nan")}], START, END)
    assert result.summary.requests.value is None
    assert result.summary.p95_latency_ms.value is None


def test_aggregate_does_not_serialize_private_provider_fields():
    result = _aggregate(
        [{"count": 1, "input": "private", "output": "private", "metadata": {"secret": "x"}}],
        START,
        END,
        operations_rows=[{"name": "chat-response", "count": 1, "traceId": "private-id"}],
    )
    serialized = result.model_dump_json()
    assert all(value not in serialized for value in ("private", "secret", "traceId"))


def test_aggregate_sorts_operation_breakdown_deterministically():
    result = _aggregate(
        [{"count": 1}],
        START,
        END,
        operations_rows=[{"name": "b", "count": 1}, {"name": "a", "count": 1}],
    )
    assert [item.name for item in result.breakdowns] == ["a", "b"]


def test_aggregate_empty_data_is_truthful():
    result = _aggregate([], START, END)
    assert result.available is False
    assert result.series == []
    assert result.summary.requests.value is None


@pytest.mark.asyncio
async def test_fetch_dashboard_skips_provider_when_tracing_disabled():
    from src.services.langfuse_dashboard import clear_dashboard_cache

    clear_dashboard_cache()
    with patch("src.services.langfuse_dashboard.tracing_enabled", return_value=False), patch(
        "src.services.langfuse_dashboard.configure_langfuse"
    ) as configure:
        result = await fetch_dashboard("7d")
    assert result.available is False
    assert result.reason == "Langfuse chưa được cấu hình."
    configure.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_dashboard_hides_provider_failure():
    from src.services.langfuse_dashboard import clear_dashboard_cache

    clear_dashboard_cache()
    with patch("src.services.langfuse_dashboard.tracing_enabled", return_value=True), patch(
        "src.services.langfuse_dashboard.configure_langfuse"
    ), patch("langfuse.get_client", side_effect=RuntimeError("secret provider failure")):
        result = await fetch_dashboard("today")
    assert result.available is False
    assert result.reason == "Langfuse hiện không phản hồi."
    assert "secret" not in result.reason


def test_metric_query_rejects_unknown_mode():
    with pytest.raises(ValueError):
        _metric_query(from_timestamp=START, to_timestamp=END, mode="untrusted")  # type: ignore[arg-type]


def test_metric_query_today_uses_hour_dimension():
    query = json.loads(_metric_query(from_timestamp=START, to_timestamp=END, time_granularity="hour"))
    assert query["timeDimension"] == {"granularity": "hour"}


def test_operation_breakdown_is_limited_to_known_fields():
    result = _aggregate(
        [{"count": 1}], START, END,
        operations_rows=[{"name": "chat-response", "count": 3, "traceId": "hidden", "metadata": "hidden"}],
    )
    assert result.breakdowns[0].name == "chat-response"
    assert result.breakdowns[0].requests == 3
    assert "traceId" not in result.breakdowns[0].model_dump()


def test_error_series_matches_day_bucket():
    result = _aggregate(
        [{"startTimeDay": "2026-08-01T00:00:00Z", "count": 5}], START, END,
        error_rows=[{"timestampDay": "2026-08-01", "count": 2}],
        errors_observable=True,
    )
    assert result.series[0].errors == 2


def test_query_mode_is_fixed_for_operations():
    query = json.loads(_metric_query(from_timestamp=START, to_timestamp=END, mode="operations"))
    assert query["dimensions"] == [{"field": "name"}]
    assert query["filters"][0]["column"] == "name"


def test_summary_missing_error_rows_is_not_zero():
    result = _aggregate([{"count": 5}], START, END, error_rows=None, errors_observable=False)
    assert result.summary.error_rate.value is None
