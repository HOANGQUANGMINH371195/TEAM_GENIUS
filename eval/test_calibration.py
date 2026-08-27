import pytest

from eval.calibration import CalibrationRecord, calibration_report


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
