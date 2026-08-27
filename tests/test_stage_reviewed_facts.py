import hashlib

import pytest

from database.corpus.stage_reviewed_facts import _source_hash_matches, validate_review_rows
from src.domain.facts import LegalFact


def _row(**overrides):
    text = "Quyền lợi được hưởng 80 phần trăm."
    row = {
        "fact_id": "fact-1",
        "subject": "người tham gia BHYT",
        "predicate": "coverage_rate",
        "normalized_value": "80%",
        "document_id": "doc-1",
        "unit_id": "unit-1",
        "source_start": 0,
        "source_end": len(text),
        "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "review_status": "accepted",
        "release_id": "snapshot-test",
        "reviewed_by": "reviewer@example.test",
        "review_note": "Verified against the canonical span.",
    }
    row.update(overrides)
    return row


def test_validation_requires_reviewer_metadata_for_accepted_facts():
    with pytest.raises(ValueError, match="reviewed_by/reviewer"):
        validate_review_rows([_row(reviewed_by="")], release_id="snapshot-test")


def test_validation_keeps_release_boundary_and_returns_typed_fact():
    validated = validate_review_rows([_row()], release_id="snapshot-test")
    assert len(validated) == 1
    fact, source = validated[0]
    assert fact.fact_id == "fact-1"
    assert source["review_note"]


def test_source_hash_must_match_canonical_span_or_unit_text():
    text = "Quyền lợi được hưởng 80 phần trăm."
    fact = LegalFact(
        fact_id="fact-1",
        subject="người tham gia BHYT",
        predicate="coverage_rate",
        normalized_value="80%",
        effective_from=None,
        effective_to=None,
        jurisdiction="",
        provision_id="",
        document_id="doc-1",
        unit_id="unit-1",
        source_start=0,
        source_end=len(text),
        source_sha256=hashlib.sha256(text.encode()).hexdigest(),
        review_status="accepted",
        release_id="snapshot-test",
    )
    assert _source_hash_matches(fact, document_text=text, unit_text="different")
    assert not _source_hash_matches(fact, document_text="tampered", unit_text="different")
