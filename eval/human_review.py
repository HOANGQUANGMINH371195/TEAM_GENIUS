"""Fail-closed validation for independent human answer reviews.

The review artifact is deliberately separate from model output and benchmark
execution.  A production attestation may reference it, but no score is
inferred from an answer hash, a model judge, or an incomplete panel.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REVIEW_ARTIFACT = "human-legal-review-v1"
REQUIRED_LABELS = (
    "factual_correct",
    "complete",
    "citation_supported",
    "catastrophic_error",
)


@dataclass(frozen=True)
class ReviewLabel:
    case_id: str
    release_id: str
    answer_sha256: str
    reviewer: str
    factual_correct: bool
    complete: bool
    citation_supported: bool
    catastrophic_error: bool


def _boolean(value: Any, *, field: str, case_id: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{case_id}: {field} must be boolean")
    return value


def _label(row: dict[str, Any], *, line_number: int, release_id: str) -> ReviewLabel:
    case_id = str(row.get("case_id") or "").strip()
    reviewer = str(row.get("reviewer") or "").strip()
    answer_sha256 = str(row.get("answer_sha256") or "").strip()
    row_release = str(row.get("release_id") or release_id).strip()
    if not case_id or not reviewer or not answer_sha256:
        raise ValueError(f"line {line_number}: case_id, reviewer and answer_sha256 are required")
    if row_release != release_id:
        raise ValueError(f"{case_id}: release mismatch")
    if len(answer_sha256) != 64 or any(char not in "0123456789abcdef" for char in answer_sha256.casefold()):
        raise ValueError(f"{case_id}: answer_sha256 must be a lowercase SHA-256 hex digest")
    values = {field: _boolean(row.get(field), field=field, case_id=case_id) for field in REQUIRED_LABELS}
    return ReviewLabel(
        case_id=case_id,
        release_id=row_release,
        answer_sha256=answer_sha256,
        reviewer=reviewer,
        **values,
    )


def load_review_artifact(path: Path) -> tuple[dict[str, Any], list[ReviewLabel]]:
    """Load a JSONL review artifact and reject malformed or machine-only rows."""
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows or not isinstance(rows[0], dict) or not isinstance(rows[0].get("manifest"), dict):
        raise ValueError("review artifact must start with a manifest")
    manifest = rows[0]["manifest"]
    if manifest.get("artifact") != REVIEW_ARTIFACT:
        raise ValueError("unsupported human review artifact")
    release_id = str(manifest.get("release_id") or "").strip()
    if not release_id.startswith("snapshot-"):
        raise ValueError("review manifest needs an immutable snapshot release_id")
    labels = [_label(row, line_number=index, release_id=release_id) for index, row in enumerate(rows[1:], start=2)]
    if not labels:
        raise ValueError("review artifact has no labels")
    if manifest.get("cases") != len({label.case_id for label in labels}):
        raise ValueError("review manifest case count mismatch")
    return manifest, labels


def validate_review_panel(
    manifest: dict[str, Any],
    labels: list[ReviewLabel],
    *,
    min_cases: int = 300,
    min_reviewers: int = 2,
) -> dict[str, Any]:
    """Require complete, unanimous independent labels for every reviewed case."""
    if min_cases < 1 or min_reviewers < 2:
        raise ValueError("invalid review panel thresholds")
    by_case: dict[str, list[ReviewLabel]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for label in labels:
        key = (label.case_id, label.reviewer)
        if key in seen:
            raise ValueError(f"duplicate reviewer label: {label.case_id}/{label.reviewer}")
        seen.add(key)
        by_case[label.case_id].append(label)
    reviewers = sorted({label.reviewer for label in labels})
    if len(reviewers) < min_reviewers:
        raise ValueError(f"review panel needs at least {min_reviewers} reviewers")
    if len(by_case) < min_cases:
        raise ValueError(f"review panel needs at least {min_cases} cases; found {len(by_case)}")
    incomplete = sorted(case_id for case_id, rows in by_case.items() if len({row.reviewer for row in rows}) < min_reviewers)
    if incomplete:
        raise ValueError("incomplete independent labels: " + ", ".join(incomplete[:10]))
    for case_id, rows in by_case.items():
        for field in REQUIRED_LABELS:
            if len({getattr(row, field) for row in rows}) != 1:
                raise ValueError(f"reviewer disagreement: {case_id}/{field}")
        if len({row.answer_sha256 for row in rows}) != 1:
            raise ValueError(f"answer hash disagreement: {case_id}")
    consensus = {case_id: rows[0] for case_id, rows in by_case.items()}
    critical = [row for row in consensus.values() if row.factual_correct]
    citation = [row for row in consensus.values() if row.citation_supported]
    return {
        "artifact": REVIEW_ARTIFACT,
        "release_id": str(manifest["release_id"]),
        "cases": len(consensus),
        "reviewers": reviewers,
        "critical_accuracy": len(critical) / len(consensus),
        "high_risk_citation_support": len(citation) / len(consensus),
        "catastrophic_errors": sum(row.catastrophic_error for row in consensus.values()),
        "panel_valid": True,
    }


def artifact_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
