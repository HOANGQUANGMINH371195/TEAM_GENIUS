import json
from pathlib import Path

import pytest

from database.neo4j.scripts.import_typed_facts import load_facts


def _row(**overrides):
    row = {
        "fact_id": "fact-1",
        "subject": "người tham gia",
        "predicate": "coverage_rate",
        "normalized_value": "80%",
        "effective_from": "2026-01-01",
        "effective_to": None,
        "jurisdiction": "VN",
        "provision_id": "u-1",
        "document_id": "d-1",
        "unit_id": "u-1",
        "source_start": 0,
        "source_end": 10,
        "source_sha256": "hash",
        "review_status": "accepted",
        "release_id": "release-1",
    }
    row.update(overrides)
    return row


def test_loader_rejects_release_mix_and_unreviewed_rows(tmp_path: Path):
    path = tmp_path / "facts.jsonl"
    path.write_text(json.dumps(_row(release_id="release-old")) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="release_id"):
        load_facts(path, release_id="release-1")

    path.write_text(json.dumps(_row(review_status="pending")) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="accepted"):
        load_facts(path, release_id="release-1")


def test_loader_accepts_valid_release_scoped_fact(tmp_path: Path):
    path = tmp_path / "facts.jsonl"
    path.write_text(json.dumps(_row()) + "\n", encoding="utf-8")
    facts = load_facts(path, release_id="release-1")
    assert len(facts) == 1
    assert facts[0].fact_id == "fact-1"
