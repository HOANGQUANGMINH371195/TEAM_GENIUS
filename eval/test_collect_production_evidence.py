from eval.collect_production_evidence import _p95, _phase_summary


def test_phase_summary_is_measurement_only_and_computes_ttft():
    rows = [
        {"status": "completed", "latency_ms": 100, "ttft_ms": 10},
        {"status": "invalid", "latency_ms": 200, "ttft_ms": None},
    ]
    report = _phase_summary(rows, kind="warm")
    assert report["cases"] == 2
    assert report["stream_error_rate"] == 0.5
    assert report["ttft_p95_seconds"] == 0.01
    assert _p95([]) is None


def test_phase_summary_keeps_route_specific_latency_when_route_is_present():
    rows = [
        {"status": "completed", "latency_ms": 100, "ttft_ms": 10, "case_kind": "temporal"},
        {"status": "completed", "latency_ms": 200, "ttft_ms": 20, "case_kind": "simple"},
        {"status": "completed", "latency_ms": 300, "ttft_ms": 30, "case_kind": "table"},
    ]
    report = _phase_summary(rows, kind="warm")
    assert report["temporal_p95_seconds"] == 0.1
    assert report["simple_p95_seconds"] == 0.2
    assert report["table_p95_seconds"] == 0.3
