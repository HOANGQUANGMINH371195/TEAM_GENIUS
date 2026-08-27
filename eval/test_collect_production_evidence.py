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
