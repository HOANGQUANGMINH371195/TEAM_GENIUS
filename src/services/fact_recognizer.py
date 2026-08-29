"""Strict adapter from reviewed extraction rows to :class:`LegalFact`.

This module is intentionally not an LLM extractor. It validates an upstream
row that already contains an explicit source span/hash, preserves pending rows
for human review, and returns rejection reasons instead of silently dropping
bad facts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from src.domain.facts import LegalFact


@dataclass(frozen=True)
class FactRecognitionResult:
    facts: tuple[LegalFact, ...]
    rejected: tuple[dict[str, object], ...]


def _date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def recognize_fact_rows(rows: list[dict[str, Any]], *, release_id: str) -> FactRecognitionResult:
    """Validate extraction rows for one release without inventing facts."""
    if not release_id.strip():
        raise ValueError("release_id is required")
    facts: list[LegalFact] = []
    rejected: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        try:
            fact = LegalFact(
                fact_id=str(row["fact_id"]),
                subject=str(row["subject"]),
                predicate=str(row["predicate"]),
                normalized_value=str(row["normalized_value"]),
                effective_from=_date(row.get("effective_from")),
                effective_to=_date(row.get("effective_to")),
                jurisdiction=str(row.get("jurisdiction") or ""),
                provision_id=str(row.get("provision_id") or ""),
                document_id=str(row["document_id"]),
                unit_id=str(row["unit_id"]),
                source_start=int(row["source_start"]) if row.get("source_start") is not None else None,
                source_end=int(row["source_end"]) if row.get("source_end") is not None else None,
                source_sha256=str(row["source_sha256"]),
                review_status=str(row.get("review_status") or "pending"),
                release_id=str(row.get("release_id") or release_id),
            )
            fact.validate()
            if fact.release_id != release_id:
                raise ValueError("release_id does not match requested release")
            if fact.fact_id in seen:
                raise ValueError("duplicate fact_id")
            seen.add(fact.fact_id)
            facts.append(fact)
        except (KeyError, TypeError, ValueError) as exc:
            rejected.append({"row": index, "reason": str(exc)[:200]})
    return FactRecognitionResult(tuple(facts), tuple(rejected))
