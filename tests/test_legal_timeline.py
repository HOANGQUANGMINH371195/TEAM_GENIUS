from datetime import date

from src.models.graph import Relation
from src.services.legal_timeline import (
    assemble_public_timeline,
    parse_legal_date,
    public_relation_type,
    state_at,
)


def test_timeline_dates_and_relation_labels_are_deterministic():
    assert parse_legal_date("01/07/2025") == date(2025, 7, 1)
    assert parse_legal_date("2025-07-01") == date(2025, 7, 1)
    assert parse_legal_date("unknown") is None
    assert public_relation_type("REL_Sua_oi_bo_sung") == "Sửa đổi, bổ sung"
    assert state_at({"effective_from": "01/07/2025"}, date(2025, 6, 30)) == "not_yet_effective"
    assert state_at({"effective_to": "01/07/2025"}, date(2025, 7, 2)) == "expired"
    assert state_at({"effective_to": "01/07/2025"}, date(2025, 6, 30)) == "unknown"


def test_timeline_hydrates_only_canonical_documents_and_never_exposes_ids():
    documents = {
        "private-a": {
            "document_number": "01/2025/QH15",
            "title": "Luật A",
            "effective_from": "2025-07-01",
        },
        "private-b": {
            "document_number": "02/2026/QH16",
            "title": "Luật B",
            "effective_from": "2026-01-01",
        },
    }
    relations = [
        Relation(
            source="Luật A",
            target="Luật B",
            source_id="private-a",
            target_id="private-b",
            relation_type="REL_Thay_the",
            adverse=True,
        ),
        Relation(
            source="Luật A",
            target="Unknown",
            source_id="private-a",
            target_id="missing-private-id",
            relation_type="REL_Can_cu",
        ),
    ]
    payload = assemble_public_timeline(
        seed_document_id="private-a",
        documents=documents,
        relations=relations,
        as_of=date(2025, 8, 1),
        degraded=False,
    )

    serialized = str(payload)
    assert "private-a" not in serialized
    assert "private-b" not in serialized
    assert "missing-private-id" not in serialized
    assert payload["events"] == [{
        "relation": "Thay thế",
        "source_document_number": "01/2025/QH15",
        "target_document_number": "02/2026/QH16",
        "adverse": True,
    }]
    assert payload["query_document"]["state_at_date"] == "effective"
