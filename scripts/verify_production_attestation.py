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
            ("topical_p95_seconds", 8.0),
            ("temporal_p95_seconds", 15.0),
            ("ttft_p95_seconds", 1.0),
            ("stream_error_rate", 0.01),
        ):
            value = _number(run, metric, errors)
            if value is not None and value > ceiling:
                errors.append(f"{prefix}.{metric}_exceeds_gate")
        availability = _number(run, "availability", errors)
        if availability is not None and availability < 0.995:
            errors.append(f"{prefix}.availability_below_gate")

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

    rollback = attestation.get("rollback")
    if not isinstance(rollback, dict) or rollback.get("canary") is not True or rollback.get("tested") is not True:
        errors.append("rollback_canary_or_test_missing")

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
