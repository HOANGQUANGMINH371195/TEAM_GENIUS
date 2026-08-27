"""Machine-checkable BHYT ontology metadata and review helpers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

ONTOLOGY_ID = "bhyt-core"
ONTOLOGY_VERSION = "1.0.0"
KNOWN_PREDICATES = frozenset(
    {
        "eligible_for",
        "has_participation_period",
        "has_care_event",
        "has_hospital_level",
        "has_referral_status",
        "has_emergency_condition",
        "coverage_rate",
        "copayment_threshold",
        "excludes",
        "effective_interval",
        "amends",
        "replaces",
        "refers_to",
    }
)


def ontology_issues(row: Mapping[str, object]) -> tuple[str, ...]:
    """Return review issues without mutating or auto-accepting a fact row."""
    issues: list[str] = []
    for field in ("fact_id", "subject", "predicate", "normalized_value", "document_id", "unit_id", "source_sha256", "release_id"):
        if not str(row.get(field) or "").strip():
            issues.append(f"missing:{field}")
    predicate = str(row.get("predicate") or "").strip()
    if predicate and predicate not in KNOWN_PREDICATES:
        issues.append(f"unknown_predicate:{predicate}")
    if str(row.get("review_status") or "pending") != "accepted":
        issues.append("not_accepted")
    if row.get("source_start") is None or row.get("source_end") is None:
        issues.append("missing:source_span")
    return tuple(issues)


def ontology_review_summary(rows: Iterable[Mapping[str, object]]) -> dict[str, object]:
    """Summarize rows for a reviewer; this is not a quality score."""
    counts = {"rows": 0, "ready": 0, "needs_review": 0}
    issues: dict[str, int] = {}
    for row in rows:
        counts["rows"] += 1
        row_issues = ontology_issues(row)
        if row_issues:
            counts["needs_review"] += 1
            for issue in row_issues:
                issues[issue] = issues.get(issue, 0) + 1
        else:
            counts["ready"] += 1
    return {
        "ontology_id": ONTOLOGY_ID,
        "ontology_version": ONTOLOGY_VERSION,
        **counts,
        "issues": dict(sorted(issues.items())),
    }
