from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from src.config import get_settings


class EmbeddingModel(Protocol):
    async def embed_query(self, text: str) -> Sequence[float]:
        """Return embedding vector for text."""


class UnconfiguredEmbeddingModel:
    async def embed_query(self, text: str) -> Sequence[float]:
        raise RuntimeError(
            "Embedding provider is not configured. Set EMBEDDING_PROVIDER and "
            "EMBEDDING_MODEL after selecting a local model runtime."
        )


def get_embedding_model() -> EmbeddingModel:
    if not get_settings().embeddings_configured:
        return UnconfiguredEmbeddingModel()
    raise NotImplementedError(
        "Selected embedding adapter is not implemented yet; configure local runtime first."
    )
