import hashlib
import json

import pytest

from database.corpus.build_community_index import build_index
from src.services.global_retrieval import build_community_summaries, drift_search


def _rows():
    return [
        {"community_id": "coverage", "document_id": "d1", "passage_id": "p1", "title": "Coverage", "ordinal": 1, "text": "Mức hưởng và điều kiện cùng chi trả."},
        {"community_id": "coverage", "document_id": "d2", "passage_id": "p2", "title": "Coverage", "ordinal": 2, "text": "Người tham gia liên tục được hưởng quyền lợi."},
        {"community_id": "referral", "document_id": "d3", "passage_id": "p3", "title": "Referral", "ordinal": 1, "text": "Giấy chuyển tuyến và cấp cứu."},
    ]


def test_community_summary_is_release_scoped_and_hash_verified():
    summaries = build_community_summaries(_rows(), release_id="snapshot-test")
    assert len(summaries) == 2
    assert summaries[0].content_sha256 == hashlib.sha256(summaries[0].text.encode()).hexdigest()
    assert summaries[0].as_record()["release_id"] == "snapshot-test"


def test_drift_search_returns_navigation_hits_not_unverified_text():
    summaries = build_community_summaries(_rows(), release_id="snapshot-test")
    hits = drift_search("điều kiện mức hưởng", summaries)
    assert hits
    assert hits[0].summary.community_id == "coverage"
    assert hits[0].document_ids
    assert hits[0].round == 1


def test_summary_builder_rejects_mixed_release_rows():
    with pytest.raises(ValueError, match="release_id"):
        build_community_summaries(
            [*_rows(), {**_rows()[0], "release_id": "snapshot-other"}],
            release_id="snapshot-test",
        )


def test_community_index_builder_is_deterministic(tmp_path):
    source = tmp_path / "communities.jsonl"
    source.write_text("\n".join(json.dumps(row) for row in _rows()) + "\n", encoding="utf-8")
    records = build_index(source, release_id="snapshot-test")
    assert len(records) == 2
    assert records[0]["release_id"] == "snapshot-test"
