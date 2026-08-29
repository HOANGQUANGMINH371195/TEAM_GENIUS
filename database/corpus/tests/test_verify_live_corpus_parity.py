from __future__ import annotations

from types import SimpleNamespace

from database.corpus.verify_live_corpus_parity import resolve_qdrant_release_collection


class _FakeQdrant:
    def __init__(self) -> None:
        self.counts = {
            "medical_legal_snapshot-c439": 14479,
            "medical_legal_hybrid_snapshot-c439": 14393,
        }

    def get_collections(self):
        return SimpleNamespace(
            collections=[SimpleNamespace(name=name) for name in self.counts]
        )

    def collection_exists(self, name: str) -> bool:
        return name in self.counts

    def count(self, name: str, **_kwargs):
        return SimpleNamespace(count=self.counts[name])


def test_resolver_skips_stale_projection_and_finds_exact_release_collection(monkeypatch):
    monkeypatch.setenv("QDRANT_COLLECTION", "medical_legal_snapshot-c439")
    collection, count = resolve_qdrant_release_collection(
        _FakeQdrant(),
        dataset_id="snapshot-c439751724ab7f10",
        expected_points=14393,
        preferred="legal_graph_chunks__snapshot-c439751724ab7f10",
    )
    assert collection == "medical_legal_hybrid_snapshot-c439"
    assert count == 14393

