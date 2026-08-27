#!/usr/bin/env python3
"""Fit a reviewed claim-confidence calibration artifact.

The input must be an independently labelled JSONL panel.  This command never
creates labels, fills missing outcomes, or treats a machine-generated answer
as human review.  It writes one self-contained JSON artifact suitable for an
offline release review; serving code may load the calibrator only after that
artifact is approved.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from eval.calibration import (
    calibration_report,
    fit_isotonic_calibrator,
    load_calibration_records,
    validate_calibration_panel,
)


def build_artifact(
    input_path: Path,
    *,
    min_cases: int = 30,
    min_reviewers: int = 2,
) -> dict[str, object]:
    records = load_calibration_records(input_path)
    panel = validate_calibration_panel(
        records, min_cases=min_cases, min_reviewers=min_reviewers
    )
    calibrator = fit_isotonic_calibrator(
        records, min_cases=min_cases, min_reviewers=min_reviewers
    )
    return {
        "artifact": "claim-calibration-v1",
        "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "panel": panel,
        "metrics": calibration_report(records),
        "calibrator": calibrator.as_dict(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="human-labelled calibration JSONL")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-cases", type=int, default=30)
    parser.add_argument("--min-reviewers", type=int, default=2)
    args = parser.parse_args()
    artifact = build_artifact(
        args.input, min_cases=args.min_cases, min_reviewers=args.min_reviewers
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(args.output), "cases": artifact["panel"]["cases"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
