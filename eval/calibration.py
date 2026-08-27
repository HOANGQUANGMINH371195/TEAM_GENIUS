"""Human-label calibration metrics for claim uncertainty.

Machine-generated gold is intentionally not accepted here. Callers must pass
records with an explicit reviewer and binary adjudication outcome before a
calibration report can be considered valid.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CalibrationRecord:
    claim_id: str
    confidence: float
    outcome: int
    reviewer: str

    def validate(self) -> None:
        if not self.claim_id.strip() or not self.reviewer.strip():
            raise ValueError("claim_id and reviewer are required")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if int(self.outcome) not in (0, 1):
            raise ValueError("outcome must be 0 or 1")


def load_calibration_records(path: Path) -> list[CalibrationRecord]:
    """Load explicit human labels from JSONL; never infer missing labels.

    The file is a review artifact, not a generated gold set.  A reviewer name
    and binary adjudication are mandatory on every row so a machine-only run
    cannot accidentally become a promotion signal.
    """
    records: list[CalibrationRecord] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("row must be an object")
            record = CalibrationRecord(
                claim_id=str(row.get("claim_id") or ""),
                confidence=float(row.get("confidence")),
                outcome=int(row.get("outcome")),
                reviewer=str(row.get("reviewer") or ""),
            )
            record.validate()
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid calibration row {line_number}: {exc}") from exc
        records.append(record)
    if not records:
        raise ValueError("calibration file has no human-labelled rows")
    return records


def calibration_report(
    records: Iterable[CalibrationRecord], *, bins: int = 10
) -> dict[str, object]:
    rows = list(records)
    if not rows:
        raise ValueError("at least one human-labelled record is required")
    if bins < 2 or bins > 100:
        raise ValueError("bins must be between 2 and 100")
    for row in rows:
        row.validate()
    bucket_data: list[dict[str, float | int]] = []
    ece = 0.0
    brier = 0.0
    for index in range(bins):
        lower = index / bins
        upper = (index + 1) / bins
        selected = [
            row for row in rows
            if (lower <= row.confidence < upper) or (index == bins - 1 and row.confidence == 1.0)
        ]
        if not selected:
            continue
        mean_confidence = sum(row.confidence for row in selected) / len(selected)
        mean_outcome = sum(row.outcome for row in selected) / len(selected)
        weight = len(selected) / len(rows)
        ece += weight * abs(mean_confidence - mean_outcome)
        bucket_data.append(
            {
                "lower": lower,
                "upper": upper,
                "count": len(selected),
                "mean_confidence": mean_confidence,
                "observed_accuracy": mean_outcome,
            }
        )
    brier = sum((row.confidence - row.outcome) ** 2 for row in rows) / len(rows)
    return {
        "cases": len(rows),
        "ece": round(ece, 8),
        "brier": round(brier, 8),
        "bins": bucket_data,
        "reviewers": sorted({row.reviewer for row in rows}),
    }
