"""Release-scoped typed legal fact contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class LegalFact:
    fact_id: str
    subject: str
    predicate: str
    normalized_value: str
    effective_from: date | None
    effective_to: date | None
    jurisdiction: str
    provision_id: str
    document_id: str
    unit_id: str
    source_start: int | None
    source_end: int | None
    source_sha256: str
    review_status: str
    release_id: str

    def as_record(self) -> dict[str, object]:
        """Serialize a fact for release-scoped SQL/Neo4j projection."""
        self.validate()
        return {
            "fact_id": self.fact_id,
            "subject": self.subject,
            "predicate": self.predicate,
            "normalized_value": self.normalized_value,
            "effective_from": self.effective_from.isoformat() if self.effective_from else None,
            "effective_to": self.effective_to.isoformat() if self.effective_to else None,
            "jurisdiction": self.jurisdiction,
            "provision_id": self.provision_id,
            "document_id": self.document_id,
            "unit_id": self.unit_id,
            "source_start": self.source_start,
            "source_end": self.source_end,
            "source_sha256": self.source_sha256,
            "review_status": self.review_status,
            "release_id": self.release_id,
        }

    def validate(self) -> None:
        required = {
            "fact_id": self.fact_id,
            "subject": self.subject,
            "predicate": self.predicate,
            "normalized_value": self.normalized_value,
            "document_id": self.document_id,
            "unit_id": self.unit_id,
            "source_sha256": self.source_sha256,
            "release_id": self.release_id,
        }
        missing = [name for name, value in required.items() if not str(value).strip()]
        if missing:
            raise ValueError("legal fact missing: " + ", ".join(missing))
        if self.source_start is not None and self.source_end is not None and self.source_end < self.source_start:
            raise ValueError("source_end must be >= source_start")
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effective_to must be >= effective_from")
        if self.review_status not in {"pending", "accepted", "rejected"}:
            raise ValueError("review_status must be pending, accepted, or rejected")
