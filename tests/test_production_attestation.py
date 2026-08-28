import hashlib
import json
from pathlib import Path

from scripts.verify_production_attestation import validate_attestation


def _valid(tmp_path: Path):
    run = {
        "kind": "cold",
        "simple_p95_seconds": 2,
        "exact_p95_seconds": 2,
        "table_p95_seconds": 2,
        "topical_p95_seconds": 4,
        "temporal_p95_seconds": 10,
        "relational_p95_seconds": 10,
        "ttft_p95_seconds": 0.8,
        "stream_error_rate": 0,
        "availability": 1,
    }
    review_path = tmp_path / "human-review.jsonl"
    review_lines = [
        {
            "manifest": {
                "artifact": "human-legal-review-v1",
                "release_id": "snapshot-test",
                "cases": 300,
            }
        }
    ]
    for index in range(300):
        for reviewer in ("legal-a", "legal-b"):
            review_lines.append(
                {
                    "case_id": f"case-{index:03d}",
                    "release_id": "snapshot-test",
                    "answer_sha256": "a" * 64,
                    "reviewer": reviewer,
                    "factual_correct": True,
                    "complete": True,
                    "citation_supported": True,
                    "catastrophic_error": False,
                }
            )
    review_path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in review_lines) + "\n",
        encoding="utf-8",
    )
    latency_path = tmp_path / "production-evidence.json"
    latency_run = {
        "kind": "cold",
        "simple_p95_seconds": 2,
        "exact_p95_seconds": 2,
        "table_p95_seconds": 2,
        "topical_p95_seconds": 4,
        "temporal_p95_seconds": 10,
        "relational_p95_seconds": 10,
        "ttft_p95_seconds": 0.8,
        "stream_error_rate": 0,
        "availability": 1,
    }
    latency_path.write_text(
        json.dumps({
            "evidence_type": "live_latency_ttft_collection",
            "dataset_id": "snapshot-test",
            "runs": [latency_run, {**latency_run, "kind": "warm"}, {**latency_run, "kind": "concurrency"}],
        }, sort_keys=True),
        encoding="utf-8",
    )
    ablation_files: dict[str, dict[str, str]] = {}
    for name in ("reranker", "typed_graph", "grounded_planning"):
        path = tmp_path / f"{name}-ablation.json"
        path.write_text(
            json.dumps({"artifact": f"{name.replace('_', '-')}-ablation-v1", "source_sha256": "a" * 64}),
            encoding="utf-8",
        )
        ablation_files[name] = {
            "artifact_path": path.name,
            "artifact_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    return {
        "release_id": "snapshot-test",
        "latency_evidence": {
            "path": latency_path.name,
            "sha256": hashlib.sha256(latency_path.read_bytes()).hexdigest(),
        },
        "runs": [run, {**run, "kind": "warm"}, {**run, "kind": "concurrency"}],
        "human_adjudication": {
            "review_artifact": review_path.name,
            "review_artifact_sha256": hashlib.sha256(review_path.read_bytes()).hexdigest(),
            "critical_accuracy": 0.97,
            "high_risk_citation_support": 1,
            "calculator_exactness": 1,
            "cost_reduction": 0.35,
            "cases": 300,
            "reviewers": 2,
            "catastrophic_errors": 0,
            "approved": True,
        },
        "outage_drills": {"graph_degraded": True, "redis_degraded": True, "provider_degraded": True},
        "ablations": {
            name: {**ablation_files[name], "reviewed": True, "no_regression": True}
            for name in ("reranker", "typed_graph", "grounded_planning")
        },
        "rollback": {"canary": True, "tested": True},
    }


def test_production_attestation_requires_artifact_metrics_to_match(tmp_path: Path):
    # This fixture intentionally demonstrates the artifact contract; the
    # reported accuracy must match the reviewed rows, not a free-form number.
    value = _valid(tmp_path)
    report = validate_attestation(value, base_dir=tmp_path)
    assert report["valid"] is False
    assert "human_adjudication.critical_accuracy_does_not_match_review_artifact" in report["errors"]


def test_production_attestation_accepts_artifact_backed_metrics(tmp_path: Path):
    value = _valid(tmp_path)
    value["human_adjudication"]["critical_accuracy"] = 1
    report = validate_attestation(value, base_dir=tmp_path)
    assert report["valid"] is True


def test_production_attestation_fails_closed_on_missing_or_bad_metrics(tmp_path: Path):
    value = _valid(tmp_path)
    value["runs"][1]["ttft_p95_seconds"] = 2
    value["human_adjudication"]["catastrophic_errors"] = 1
    value["outage_drills"]["graph_degraded"] = False
    report = validate_attestation(value, base_dir=tmp_path)
    assert report["valid"] is False
    assert "runs[1].ttft_p95_seconds_exceeds_gate" in report["errors"]
    assert "human_adjudication.catastrophic_errors_not_zero" in report["errors"]
    value["latency_evidence"]["sha256"] = "b" * 64
    report = validate_attestation(value, base_dir=tmp_path)
    assert "latency_evidence.hash_mismatch" in report["errors"]
    value["latency_evidence"]["sha256"] = hashlib.sha256(
        (tmp_path / "production-evidence.json").read_bytes()
    ).hexdigest()
    value["ablations"]["reranker"]["artifact_sha256"] = "b" * 64
    report = validate_attestation(value, base_dir=tmp_path)
    assert "ablations.reranker.artifact_hash_mismatch" in report["errors"]
