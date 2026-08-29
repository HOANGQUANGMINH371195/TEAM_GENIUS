from src.services.fact_recognizer import recognize_fact_rows


def _row(**overrides):
    row = {
        "fact_id": "f1",
        "subject": "nhóm tham gia",
        "predicate": "coverage_rate",
        "normalized_value": "80%",
        "document_id": "d1",
        "unit_id": "u1",
        "source_start": 0,
        "source_end": 5,
        "source_sha256": "hash",
    }
    row.update(overrides)
    return row


def test_recognizer_preserves_pending_rows_for_review():
    result = recognize_fact_rows([_row()], release_id="snapshot-1")
    assert len(result.facts) == 1
    assert result.facts[0].review_status == "pending"
    assert result.rejected == ()


def test_recognizer_reports_bad_rows_without_silent_drop():
    result = recognize_fact_rows(
        [_row(), _row(fact_id="f2", source_end=-1)], release_id="snapshot-1"
    )
    assert len(result.facts) == 1
    assert result.rejected[0]["row"] == 1
