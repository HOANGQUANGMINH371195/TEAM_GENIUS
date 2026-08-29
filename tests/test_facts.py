from datetime import date

import pytest

from src.domain.facts import LegalFact


def test_legal_fact_requires_release_provenance():
    fact = LegalFact(
        fact_id="f1", subject="group", predicate="coverage_rate", normalized_value="80%",
        effective_from=date(2025, 1, 1), effective_to=None, jurisdiction="VN",
        provision_id="u1", document_id="d1", unit_id="u1", source_start=0, source_end=4,
        source_sha256="hash", review_status="accepted", release_id="snapshot-test",
    )
    fact.validate()


def test_legal_fact_rejects_invalid_span():
    fact = LegalFact(
        fact_id="f1", subject="group", predicate="rate", normalized_value="80%",
        effective_from=None, effective_to=None, jurisdiction="VN", provision_id="u1",
        document_id="d1", unit_id="u1", source_start=4, source_end=2,
        source_sha256="hash", review_status="accepted", release_id="snapshot-test",
    )
    with pytest.raises(ValueError, match="source_end"):
        fact.validate()


def test_legal_fact_record_is_release_scoped_and_serializable():
    fact = LegalFact(
        fact_id="f2", subject="người tham gia", predicate="coverage_rate", normalized_value="80%",
        effective_from=date(2026, 1, 1), effective_to=None, jurisdiction="VN",
        provision_id="u2", document_id="d2", unit_id="u2", source_start=10, source_end=20,
        source_sha256="hash-2", review_status="accepted", release_id="snapshot-test",
    )
    record = fact.as_record()
    assert record["release_id"] == "snapshot-test"
    assert record["effective_from"] == "2026-01-01"
    assert record["source_sha256"] == "hash-2"
