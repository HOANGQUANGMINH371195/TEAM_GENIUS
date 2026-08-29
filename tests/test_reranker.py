from src.models.graph import RetrievalResult
from src.services.reranker import cross_encoder_rerank


def test_cross_encoder_backend_has_safe_fallback(monkeypatch):
    monkeypatch.setattr("src.services.reranker._load_cross_encoder", lambda _name: None)
    hit = RetrievalResult(chunk_id="c1", document_id="d1", content="nội dung")
    ranked, status = cross_encoder_rerank("câu hỏi", [hit], model_name="missing")
    assert ranked[0].chunk_id == "c1"
    assert status == "fallback_unavailable"


def test_cross_encoder_reranks_bounded_head(monkeypatch):
    class FakeEncoder:
        def predict(self, pairs, **_kwargs):
            return [0.1 if "thấp" in text else 0.9 for _, text in pairs]

    monkeypatch.setattr("src.services.reranker._load_cross_encoder", lambda _name: FakeEncoder())
    low = RetrievalResult(chunk_id="low", document_id="d", content="thấp")
    high = RetrievalResult(chunk_id="high", document_id="d", content="cao")
    ranked, status = cross_encoder_rerank("câu hỏi", [low, high], model_name="fake")
    assert status == "cross_encoder"
    assert [item.chunk_id for item in ranked] == ["high", "low"]
    assert ranked[0].rank_details["reranker_backend"] == "cross_encoder"
