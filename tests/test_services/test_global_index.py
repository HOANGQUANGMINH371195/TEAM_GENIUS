import hashlib
import json

from src.config import Settings
from src.services import chat
from src.services.chat import GraphRagRuntime


def test_runtime_loads_release_scoped_community_index(tmp_path, monkeypatch):
    release_id = "snapshot-test"
    text = "Mức hưởng và điều kiện cùng chi trả."
    path = tmp_path / "community-index.jsonl"
    rows = [
        {
            "index": "community-summary-v1",
            "release_id": release_id,
            "communities": 1,
            "source_sha256": "source",
        },
        {
            "community_id": "coverage",
            "release_id": release_id,
            "title": "Coverage",
            "document_ids": ["doc-1"],
            "text": text,
            "source_passage_ids": ["passage-1"],
            "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    settings = Settings(
        feature_global_search_enabled=True,
        community_index_path=str(path),
        retrieval_candidate_k=10,
    )
    monkeypatch.setattr(chat, "get_settings", lambda: settings)
    runtime = GraphRagRuntime()
    assert runtime._global_document_ids("điều kiện mức hưởng", release_id=release_id) == ["doc-1"]
    assert runtime._global_document_ids("điều kiện mức hưởng", release_id="snapshot-other") == []
