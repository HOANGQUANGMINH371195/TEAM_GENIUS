import json

import pytest

from eval.calibrate_claims import build_artifact
from eval.calibration import (
    CalibrationRecord,
    calibration_report,
    fit_isotonic_calibrator,
    load_calibration_records,
    validate_calibration_panel,
)


def test_calibration_requires_human_reviewer_and_reports_ece():
    rows = [
        CalibrationRecord("c1", 0.9, 1, "reviewer-a"),
        CalibrationRecord("c2", 0.2, 0, "reviewer-a"),
    ]
    report = calibration_report(rows, bins=2)
    assert report["cases"] == 2
    assert report["ece"] == 0.15
    assert report["reviewers"] == ["reviewer-a"]


def test_calibration_rejects_machine_or_invalid_labels():
    with pytest.raises(ValueError, match="reviewer"):
        calibration_report([CalibrationRecord("c1", 0.5, 1, "")])
    with pytest.raises(ValueError, match="outcome"):
        calibration_report([CalibrationRecord("c1", 0.5, 2, "human")])


def test_calibration_loader_requires_explicit_human_rows(tmp_path):
    path = tmp_path / "labels.jsonl"
    path.write_text('{"claim_id":"c1","confidence":0.8,"outcome":1,"reviewer":"human-a"}\n', encoding="utf-8")
    rows = load_calibration_records(path)
    assert rows == [CalibrationRecord("c1", 0.8, 1, "human-a")]

    path.write_text('{"claim_id":"c1","confidence":0.8,"outcome":1}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="reviewer"):
        load_calibration_records(path)


def test_calibration_panel_requires_independent_complete_labels():
    rows = [
        CalibrationRecord("c1", 0.9, 1, "reviewer-a"),
        CalibrationRecord("c1", 0.9, 1, "reviewer-b"),
        CalibrationRecord("c2", 0.2, 0, "reviewer-a"),
        CalibrationRecord("c2", 0.2, 1, "reviewer-b"),
    ]
    report = validate_calibration_panel(rows, min_cases=2)
    assert report["panel_valid"] is True
    assert report["raw_pair_agreement"] == 0.5

    with pytest.raises(ValueError, match="independent labels"):
        validate_calibration_panel(rows[:3], min_cases=1)

    with pytest.raises(ValueError, match="duplicate reviewer label"):
        validate_calibration_panel([*rows, rows[0]], min_cases=2)


def test_isotonic_calibrator_is_monotone_and_aggregates_claims():
    rows = []
    labels = [("c1", 0.2, 1), ("c2", 0.4, 0), ("c3", 0.8, 1)]
    for claim_id, confidence, outcome in labels:
        rows.extend([
            CalibrationRecord(claim_id, confidence, outcome, "reviewer-a"),
            CalibrationRecord(claim_id, confidence, outcome, "reviewer-b"),
        ])
    calibrator = fit_isotonic_calibrator(rows, min_cases=3)
    assert calibrator.cases == 3
    assert calibrator.predict(0.1) <= calibrator.predict(0.9)
    assert calibrator.as_dict()["method"] == "pool_adjacent_violators"


def test_isotonic_calibrator_refuses_insufficient_reviewed_cases():
    rows = [
        CalibrationRecord("c1", 0.8, 1, "reviewer-a"),
        CalibrationRecord("c1", 0.8, 1, "reviewer-b"),
    ]
    with pytest.raises(ValueError, match="at least 30 claims"):
        fit_isotonic_calibrator(rows)


def test_calibration_artifact_is_hashable_and_self_describing(tmp_path):
    path = tmp_path / "labels.jsonl"
    lines = []
    for index in range(30):
        for reviewer in ("reviewer-a", "reviewer-b"):
            lines.append(json.dumps({
                "claim_id": f"c{index}",
                "confidence": index / 29,
                "outcome": int(index >= 15),
                "reviewer": reviewer,
            }))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    artifact = build_artifact(path)
    assert artifact["artifact"] == "claim-calibration-v1"
    assert len(artifact["input_sha256"]) == 64
    assert artifact["panel"]["cases"] == 30
