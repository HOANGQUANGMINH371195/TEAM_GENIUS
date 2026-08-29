#!/usr/bin/env python3
"""Run a claim-level Auditor ablation on an independently reviewed artifact.

The input is deliberately explicit: every claim needs an evidence score,
three reviewer outcomes, and a canonical source hash.  The evaluator measures
agreement with the review panel only; it never manufactures legal gold from
the model's own answer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from statistics import mean
from typing import Any


def _read(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if rows and isinstance(rows[0], dict) and "manifest" in rows[0]:
        rows = rows[1:]
    if not rows:
        raise ValueError("auditor artifact has no claims")
    claims: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not str(row.get("claim_id") or "").strip():
            raise ValueError("each auditor row needs claim_id")
        labels = row.get("review_labels")
        if not isinstance(labels, list) or len(labels) < 2:
            raise ValueError(f"{row['claim_id']}: at least two reviewer labels are required")
        reviewers = {str(item.get("reviewer") or "").strip() for item in labels if isinstance(item, dict)}
        if "" in reviewers or len(reviewers) < 2:
            raise ValueError(f"{row['claim_id']}: independent reviewer IDs are required")
        outcomes = {bool(item.get("accepted")) for item in labels if isinstance(item, dict)}
        if len(outcomes) != 1:
            raise ValueError(f"{row['claim_id']}: reviewer disagreement must be resolved before ablation")
        if not str(row.get("source_sha256") or "").strip():
            raise ValueError(f"{row['claim_id']}: canonical source_sha256 is required")
        for field in ("evidence_support", "faithfulness", "factuality", "completeness"):
            try:
                value = float(row.get(field))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{row['claim_id']}: {field} must be numeric") from exc
            if not 0 <= value <= 1:
                raise ValueError(f"{row['claim_id']}: {field} must be in [0,1]")
        claims.append(row)
    if len({str(row["claim_id"]) for row in claims}) != len(claims):
        raise ValueError("duplicate claim_id")
    return claims


def _decisions(row: dict[str, Any]) -> dict[str, bool]:
    evidence = float(row["evidence_support"])
    lexical = evidence >= 0.5 and bool(str(row.get("source_sha256") or "").strip())
    calibrated = all(float(row[field]) >= threshold for field, threshold in (
        ("faithfulness", 0.70), ("factuality", 0.80), ("completeness", 0.70)
    )) and lexical
    return {"no_auditor": True, "lexical_auditor": lexical, "calibrated_auditor": calibrated}


def _metrics(claims: list[dict[str, Any]], variant: str) -> dict[str, Any]:
    rows = []
    for row in claims:
        gold = bool(row["review_labels"][0]["accepted"])
        predicted = _decisions(row)[variant]
        rows.append((gold, predicted))
    tp = sum(gold and pred for gold, pred in rows)
    fp = sum(not gold and pred for gold, pred in rows)
    fn = sum(gold and not pred for gold, pred in rows)
    tn = sum(not gold and not pred for gold, pred in rows)
    return {
        "cases": len(rows),
        "accuracy": (tp + tn) / len(rows),
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "unsupported_accepts": fp,
        "catastrophic_errors": fp,
    }


def run_ablation(path: Path) -> dict[str, Any]:
    claims = _read(path)
    return {
        "artifact": "auditor-ablation-v1",
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "reviewers": sorted({str(label["reviewer"]) for row in claims for label in row["review_labels"]}),
        "variants": {name: _metrics(claims, name) for name in ("no_auditor", "lexical_auditor", "calibrated_auditor")},
        "score_definition": "agreement with unanimous independent reviewer labels; disagreement is rejected",
        "mean_confidence": mean(float(row["faithfulness"]) for row in claims),
        "promotion_ready": False,
        "warning": "This is a review-process/claim-decision ablation, not a substitute for legal adjudication.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_ablation(args.artifact)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"artifact": report["artifact"], "cases": report["variants"]["no_auditor"]["cases"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
