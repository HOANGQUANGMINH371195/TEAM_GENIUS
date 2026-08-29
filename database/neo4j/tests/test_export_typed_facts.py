from datetime import date

from database.neo4j.scripts.export_typed_facts import serialize_fact_row


def test_export_serializes_only_accepted_release_fields():
    row = {
        "fact_id": "f-1",
        "subject": "người tham gia",
        "predicate": "coverage_rate",
        "normalized_value": "100%",
        "effective_from": date(2025, 1, 1),
        "effective_to": None,
        "jurisdiction": "VN",
        "provision_id": "Điều 22",
        "document_id": "doc-1",
        "unit_id": "unit-1",
        "source_start": 10,
        "source_end": 20,
        "source_sha256": "abc",
        "review_status": "accepted",
        "payload": {"secret": "must-not-export"},
    }
    exported = serialize_fact_row(row, release_id="snapshot-test")
    assert exported["effective_from"] == "2025-01-01"
    assert exported["release_id"] == "snapshot-test"
    assert exported["review_status"] == "accepted"
    assert "payload" not in exported

