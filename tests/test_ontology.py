from src.domain.ontology import ontology_issues, ontology_review_summary


def _row(**overrides):
    row = {
        "fact_id": "f1", "subject": "người tham gia", "predicate": "coverage_rate",
        "normalized_value": "80%", "document_id": "d1", "unit_id": "u1",
        "source_sha256": "hash", "source_start": 0, "source_end": 10,
        "review_status": "accepted", "release_id": "snapshot-test",
    }
    row.update(overrides)
    return row


def test_ontology_requires_reviewable_provenance_and_known_predicate():
    assert ontology_issues(_row()) == ()
    issues = ontology_issues(_row(predicate="made_up", source_end=None))
    assert "unknown_predicate:made_up" in issues
    assert "missing:source_span" in issues


def test_ontology_summary_never_promotes_pending_rows():
    summary = ontology_review_summary([_row(), _row(review_status="pending")])
    assert summary["ready"] == 1
    assert summary["needs_review"] == 1
    assert summary["issues"]["not_accepted"] == 1
