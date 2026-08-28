#!/usr/bin/env python3
"""Validate the evidence bundle required to promote a release.

This is intentionally a verifier, not a benchmark runner. It accepts only an
operator-produced JSON attestation containing the independent human review,
latency/TTFT runs, outage drills, ablations, cost ledger and rollback result.
Missing fields fail closed and no metric is inferred from repository tests.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from eval.human_review import artifact_sha256, load_review_artifact, validate_review_panel


def _number(mapping: dict[str, Any], key: str, errors: list[str]) -> float | None:
    value = mapping.get(key)
    try:
        number = float(value)
    except (TypeError, ValueError):
        errors.append(f"missing_or_invalid:{key}")
        return None
    if not number == number or number in {float("inf"), float("-inf")}:
        errors.append(f"non_finite:{key}")
        return None
    return number


def _validate_review_artifact(
    human: dict[str, Any],
    *,
    release_id: str,
    base_dir: Path | None,
    errors: list[str],
) -> dict[str, Any] | None:
    artifact_value = str(human.get("review_artifact") or "").strip()
    expected_hash = str(human.get("review_artifact_sha256") or "").strip().casefold()
    if not artifact_value:
        errors.append("human_adjudication.review_artifact_required")
        return None

    if len(expected_hash) != 64 or any(char not in "0123456789abcdef" for char in expected_hash):
        errors.append("human_adjudication.review_artifact_sha256_required")
        return None
    artifact_path = Path(artifact_value)
    if not artifact_path.is_absolute() and base_dir is not None:
        artifact_path = base_dir / artifact_path
    try:
        if artifact_sha256(artifact_path) != expected_hash:
            errors.append("human_adjudication.review_artifact_hash_mismatch")
            return None
        manifest, labels = load_review_artifact(artifact_path)
        if str(manifest.get("release_id")) != release_id:
            errors.append("human_adjudication.review_artifact_release_mismatch")
            return None
        return validate_review_panel(manifest, labels, min_cases=300, min_reviewers=2)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"human_adjudication.review_artifact_invalid:{type(exc).__name__}")
        return None


def _validate_latency_artifact(
    attestation: dict[str, Any],
    *,
    release_id: str,
    base_dir: Path | None,
    errors: list[str],
) -> None:
    evidence = attestation.get("latency_evidence")
    if not isinstance(evidence, dict):
        errors.append("latency_evidence_required")
        return
    path_value = str(evidence.get("path") or "").strip()
    expected_hash = str(evidence.get("sha256") or "").strip().casefold()
    if not path_value:
        errors.append("latency_evidence.path_required")
        return
    if len(expected_hash) != 64 or any(char not in "0123456789abcdef" for char in expected_hash):
        errors.append("latency_evidence.sha256_required")
        return
    path = Path(path_value)
    if not path.is_absolute() and base_dir is not None:
        path = base_dir / path
    try:
        if artifact_sha256(path) != expected_hash:
            errors.append("latency_evidence.hash_mismatch")
            return
        artifact = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(artifact, dict) or artifact.get("evidence_type") != "live_latency_ttft_collection":
            errors.append("latency_evidence.type_invalid")
            return
        if str(artifact.get("dataset_id") or "") != release_id:
            errors.append("latency_evidence.release_mismatch")
            return
        runs = artifact.get("runs")
        if not isinstance(runs, list):
            errors.append("latency_evidence.runs_required")
            return
        by_kind = {str(run.get("kind")): run for run in runs if isinstance(run, dict)}
        if set(by_kind) != {"cold", "warm", "concurrency"}:
            errors.append("latency_evidence.runs_must_include_cold_warm_concurrency")
            return
        attestation_runs = {
            str(run.get("kind")): run for run in attestation.get("runs", []) if isinstance(run, dict)
        }
        route_metrics = (
            "simple_p95_seconds", "exact_p95_seconds", "table_p95_seconds",
            "topical_p95_seconds", "temporal_p95_seconds", "relational_p95_seconds",
            "ttft_p95_seconds", "stream_error_rate", "availability",
        )
        for kind, artifact_run in by_kind.items():
            if kind not in attestation_runs:
                errors.append(f"latency_evidence.attestation_run_missing:{kind}")
                continue
            for metric in route_metrics:
                value = artifact_run.get(metric)
                if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                    errors.append(f"latency_evidence.{kind}.{metric}_invalid")
                    continue
                try:
                    supplied = float(attestation_runs[kind].get(metric))
                except (TypeError, ValueError):
                    errors.append(f"latency_evidence.{kind}.{metric}_attestation_invalid")
                    continue
                if not math.isclose(supplied, float(value), rel_tol=0.0, abs_tol=1e-9):
                    errors.append(f"latency_evidence.{kind}.{metric}_does_not_match")
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        errors.append("latency_evidence.invalid")


def _validate_ablation_artifact(
    item: dict[str, Any],
    *,
    name: str,
    base_dir: Path | None,
    errors: list[str],
) -> None:
    path_value = str(item.get("artifact_path") or "").strip()
    expected_hash = str(item.get("artifact_sha256") or "").strip().casefold()
    if not path_value:
        errors.append(f"ablations.{name}.artifact_path_required")
        return
    if len(expected_hash) != 64 or any(char not in "0123456789abcdef" for char in expected_hash):
        errors.append(f"ablations.{name}.artifact_sha256_required")
        return
    path = Path(path_value)
    if not path.is_absolute() and base_dir is not None:
        path = base_dir / path
    try:
        if artifact_sha256(path) != expected_hash:
            errors.append(f"ablations.{name}.artifact_hash_mismatch")
            return
        artifact = json.loads(path.read_text(encoding="utf-8"))
        expected_type = f"{name.replace('_', '-')}-ablation-v1"
        if not isinstance(artifact, dict) or artifact.get("artifact") != expected_type:
            errors.append(f"ablations.{name}.artifact_type_invalid")
            return
        if not str(artifact.get("source_sha256") or "").strip():
            errors.append(f"ablations.{name}.source_sha256_required")
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        errors.append(f"ablations.{name}.artifact_invalid")


def _validate_operations_artifact(
    attestation: dict[str, Any],
    *,
    release_id: str,
    base_dir: Path | None,
    errors: list[str],
) -> None:
    evidence = attestation.get("operations_evidence")
    if not isinstance(evidence, dict):
        errors.append("operations_evidence_required")
        return
    path_value = str(evidence.get("path") or "").strip()
    expected_hash = str(evidence.get("sha256") or "").strip().casefold()
    if not path_value:
        errors.append("operations_evidence.path_required")
        return
    if len(expected_hash) != 64 or any(char not in "0123456789abcdef" for char in expected_hash):
        errors.append("operations_evidence.sha256_required")
        return
    path = Path(path_value)
    if not path.is_absolute() and base_dir is not None:
        path = base_dir / path
    try:
        if artifact_sha256(path) != expected_hash:
            errors.append("operations_evidence.hash_mismatch")
            return
        artifact = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(artifact, dict) or artifact.get("artifact") != "operations-evidence-v1":
            errors.append("operations_evidence.type_invalid")
            return
        if str(artifact.get("release_id") or "") != release_id:
            errors.append("operations_evidence.release_mismatch")
            return
        drills = artifact.get("outage_drills")
        rollback = artifact.get("rollback")
        if not isinstance(drills, dict) or any(drills.get(name) is not True for name in ("graph_degraded", "redis_degraded", "provider_degraded")):
            errors.append("operations_evidence.outage_drills_not_passed")
        if not isinstance(rollback, dict) or rollback.get("canary") is not True or rollback.get("tested") is not True:
            errors.append("operations_evidence.rollback_not_passed")
        attestation_drills = attestation.get("outage_drills") if isinstance(attestation.get("outage_drills"), dict) else {}
        for name in ("graph_degraded", "redis_degraded", "provider_degraded"):
            if attestation_drills.get(name) is not artifact.get("outage_drills", {}).get(name):
                errors.append(f"operations_evidence.{name}_does_not_match")
        attestation_rollback = attestation.get("rollback") if isinstance(attestation.get("rollback"), dict) else {}
        for name in ("canary", "tested"):
            if attestation_rollback.get(name) is not artifact.get("rollback", {}).get(name):
                errors.append(f"operations_evidence.rollback_{name}_does_not_match")
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        errors.append("operations_evidence.invalid")


def _validate_cost_artifact(
    attestation: dict[str, Any],
    *,
    release_id: str,
    base_dir: Path | None,
    human: dict[str, Any],
    errors: list[str],
) -> None:
    evidence = attestation.get("cost_evidence")
    if not isinstance(evidence, dict):
        errors.append("cost_evidence_required")
        return
    path_value = str(evidence.get("path") or "").strip()
    expected_hash = str(evidence.get("sha256") or "").strip().casefold()
    if not path_value:
        errors.append("cost_evidence.path_required")
        return
    if len(expected_hash) != 64 or any(char not in "0123456789abcdef" for char in expected_hash):
        errors.append("cost_evidence.sha256_required")
        return
    path = Path(path_value)
    if not path.is_absolute() and base_dir is not None:
        path = base_dir / path
    try:
        if artifact_sha256(path) != expected_hash:
            errors.append("cost_evidence.hash_mismatch")
            return
        artifact = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(artifact, dict) or artifact.get("artifact") != "cost-ledger-v1":
            errors.append("cost_evidence.type_invalid")
            return
        if str(artifact.get("release_id") or "") != release_id:
            errors.append("cost_evidence.release_mismatch")
            return
        baseline = float(artifact.get("baseline_cost_usd"))
        candidate = float(artifact.get("candidate_cost_usd"))
        if not math.isfinite(baseline) or not math.isfinite(candidate) or baseline <= 0 or candidate < 0:
            errors.append("cost_evidence.cost_values_invalid")
            return
        receipts = artifact.get("provider_receipts")
        if not isinstance(receipts, list) or not receipts:
            errors.append("cost_evidence.provider_receipts_required")
            return
        reduction = (baseline - candidate) / baseline
        if reduction < 0.30:
            errors.append("cost_evidence.reduction_below_gate")
        try:
            supplied = float(human.get("cost_reduction"))
        except (TypeError, ValueError):
            errors.append("human_adjudication.cost_reduction_must_match_cost_artifact")
            return
        if not math.isclose(supplied, reduction, rel_tol=0.0, abs_tol=1e-9):
            errors.append("human_adjudication.cost_reduction_does_not_match_cost_artifact")
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        errors.append("cost_evidence.invalid")
def validate_attestation(
    attestation: dict[str, Any], *, base_dir: Path | None = None
) -> dict[str, Any]:
    errors: list[str] = []
    release_id = str(attestation.get("release_id") or "")
    if not release_id.startswith("snapshot-"):
        errors.append("release_id_must_be_immutable_snapshot")

    runs = attestation.get("runs")
    if not isinstance(runs, list):
        errors.append("runs_required")
        runs = []
    run_kinds = {str(run.get("kind")) for run in runs if isinstance(run, dict)}
    required_kinds = {"cold", "warm", "concurrency"}
    if run_kinds != required_kinds:
        errors.append("runs_must_include_cold_warm_concurrency")
    for index, run in enumerate(runs):
        if not isinstance(run, dict):
            errors.append(f"run_{index}_must_be_object")
            continue
        prefix = f"runs[{index}]"
        for metric, ceiling in (
            ("simple_p95_seconds", 5.0),
            ("exact_p95_seconds", 5.0),
            ("table_p95_seconds", 5.0),
            ("topical_p95_seconds", 8.0),
            ("temporal_p95_seconds", 15.0),
            ("relational_p95_seconds", 15.0),
            ("ttft_p95_seconds", 1.0),
            ("stream_error_rate", 0.01),
        ):
            value = _number(run, metric, errors)
            if value is not None and value > ceiling:
                errors.append(f"{prefix}.{metric}_exceeds_gate")
        availability = _number(run, "availability", errors)
        if availability is not None and availability < 0.995:
            errors.append(f"{prefix}.availability_below_gate")

    _validate_latency_artifact(
        attestation, release_id=release_id, base_dir=base_dir, errors=errors
    )

    human = attestation.get("human_adjudication")
    if not isinstance(human, dict):
        errors.append("human_adjudication_required")
        human = {}
    for metric, floor in (
        ("critical_accuracy", 0.95),
        ("high_risk_citation_support", 0.98),
        ("calculator_exactness", 1.0),
        ("cost_reduction", 0.30),
    ):
        value = _number(human, metric, errors)
        if value is not None and value < floor:
            errors.append(f"human_adjudication.{metric}_below_gate")
    cases = _number(human, "cases", errors)
    reviewers = _number(human, "reviewers", errors)
    if cases is not None and cases < 300:
        errors.append("human_adjudication.cases_below_300")
    if reviewers is not None and reviewers < 2:
        errors.append("human_adjudication.needs_two_reviewers")
    if human.get("catastrophic_errors") != 0:
        errors.append("human_adjudication.catastrophic_errors_not_zero")
    if human.get("approved") is not True:
        errors.append("human_adjudication.not_approved")
    review_summary = _validate_review_artifact(
        human, release_id=release_id, base_dir=base_dir, errors=errors
    )
    if review_summary is not None:
        expected = {
            "cases": float(review_summary["cases"]),
            "reviewers": float(len(review_summary["reviewers"])),
            "critical_accuracy": float(review_summary["critical_accuracy"]),
            "high_risk_citation_support": float(review_summary["high_risk_citation_support"]),
            "catastrophic_errors": float(review_summary["catastrophic_errors"]),
        }
        for field, value in expected.items():
            try:
                supplied = float(human.get(field))
            except (TypeError, ValueError):
                errors.append(f"human_adjudication.{field}_must_match_review_artifact")
                continue
            if not math.isclose(supplied, value, rel_tol=0.0, abs_tol=1e-9):
                errors.append(f"human_adjudication.{field}_does_not_match_review_artifact")

    _validate_cost_artifact(
        attestation, release_id=release_id, base_dir=base_dir, human=human, errors=errors
    )

    drills = attestation.get("outage_drills")
    if not isinstance(drills, dict):
        errors.append("outage_drills_required")
        drills = {}
    for name in ("graph_degraded", "redis_degraded", "provider_degraded"):
        if drills.get(name) is not True:
            errors.append(f"outage_drills.{name}_not_passed")

    ablations = attestation.get("ablations")
    if not isinstance(ablations, dict):
        errors.append("ablations_required")
        ablations = {}
    for name in ("reranker", "typed_graph", "grounded_planning"):
        item = ablations.get(name)
        if not isinstance(item, dict) or item.get("reviewed") is not True or item.get("no_regression") is not True:
            errors.append(f"ablations.{name}_not_approved")
        if isinstance(item, dict):
            _validate_ablation_artifact(item, name=name, base_dir=base_dir, errors=errors)

    rollback = attestation.get("rollback")
    if not isinstance(rollback, dict) or rollback.get("canary") is not True or rollback.get("tested") is not True:
        errors.append("rollback_canary_or_test_missing")

    _validate_operations_artifact(
        attestation, release_id=release_id, base_dir=base_dir, errors=errors
    )

    return {
        "valid": not errors,
        "release_id": release_id,
        "errors": errors,
        "rule": "All required independent evidence must be present; no metric is inferred.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("attestation", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        value = json.loads(args.attestation.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("attestation must be a JSON object")
        report = validate_attestation(value, base_dir=args.attestation.parent)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        report = {"valid": False, "errors": [f"invalid_json:{exc}"]}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"valid": report["valid"], "errors": len(report.get("errors", []))}))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
