"""Optional local cross-encoder reranker with a deterministic fallback."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from src.models.graph import RetrievalResult


@lru_cache(maxsize=2)
def _load_cross_encoder(model_name: str) -> Any | None:
    if not model_name.strip():
        return None
    try:
        from sentence_transformers import CrossEncoder

        return CrossEncoder(model_name)
    except Exception:
        # The optional package/model is deliberately not part of the default
        # runtime image. Heuristic reranking remains the safe fallback.
        return None


def cross_encoder_rerank(
    query: str,
    hits: list[RetrievalResult],
    *,
    model_name: str,
    max_candidates: int = 30,
) -> tuple[list[RetrievalResult], str]:
    """Rerank a bounded candidate head; return ``(hits, backend_status)``."""
    bounded = [item.model_copy(deep=True) for item in hits[: max(1, min(max_candidates, 64))]]
    encoder = _load_cross_encoder(model_name)
    if encoder is None or not bounded:
        return hits, "fallback_unavailable"
    try:
        pairs = [(query, f"{item.section_title}\n{item.content}") for item in bounded]
        scores = encoder.predict(pairs, batch_size=min(16, len(pairs)), show_progress_bar=False)
        for item, score in zip(bounded, scores, strict=True):
            item.score = float(score)
            item.rank_details = {
                **item.rank_details,
                "cross_encoder_score": float(score),
                "reranker_backend": "cross_encoder",
            }
        bounded.sort(key=lambda item: (-item.score, item.document_id, item.chunk_id))
        tail = [item for item in hits if item.chunk_id not in {candidate.chunk_id for candidate in bounded}]
        return [*bounded, *tail], "cross_encoder"
    except Exception:
        return hits, "fallback_error"
