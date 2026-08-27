from scripts.verify_production_attestation import validate_attestation


def _valid():
    run = {
        "kind": "cold",
        "simple_p95_seconds": 2,
        "topical_p95_seconds": 4,
        "temporal_p95_seconds": 10,
        "ttft_p95_seconds": 0.8,
        "stream_error_rate": 0,
        "availability": 1,
    }
    return {
        "release_id": "snapshot-test",
        "runs": [run, {**run, "kind": "warm"}, {**run, "kind": "concurrency"}],
        "human_adjudication": {
            "critical_accuracy": 0.97,
            "high_risk_citation_support": 0.99,
            "calculator_exactness": 1,
            "cost_reduction": 0.35,
            "cases": 300,
            "reviewers": 2,
            "catastrophic_errors": 0,
            "approved": True,
        },
        "outage_drills": {"graph_degraded": True, "redis_degraded": True, "provider_degraded": True},
        "ablations": {
            "reranker": {"reviewed": True, "no_regression": True},
            "typed_graph": {"reviewed": True, "no_regression": True},
            "grounded_planning": {"reviewed": True, "no_regression": True},
        },
        "rollback": {"canary": True, "tested": True},
    }


def test_production_attestation_requires_all_independent_evidence():
    report = validate_attestation(_valid())
    assert report["valid"] is True


def test_production_attestation_fails_closed_on_missing_or_bad_metrics():
    value = _valid()
    value["runs"][1]["ttft_p95_seconds"] = 2
    value["human_adjudication"]["catastrophic_errors"] = 1
    value["outage_drills"]["graph_degraded"] = False
    report = validate_attestation(value)
    assert report["valid"] is False
    assert "runs[1].ttft_p95_seconds_exceeds_gate" in report["errors"]
    assert "human_adjudication.catastrophic_errors_not_zero" in report["errors"]
