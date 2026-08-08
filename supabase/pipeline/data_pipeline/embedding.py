"""Lazy local embedding runtime shared by API and Supabase workers."""

from __future__ import annotations

import os
from typing import Any

DEFAULT_MODEL = "huyydangg/DEk21_hcmute_embedding_v2"
PREPROCESSOR = "pyvi.ViTokenizer"
_MODEL: Any | None = None


def _configured_device() -> str:
    import torch

    device = os.getenv("EMBEDDING_DEVICE", "cuda:0").strip() or "cuda:0"
    if device.casefold().startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable; set EMBEDDING_DEVICE=cpu explicitly")
    return device


def _model() -> Any:
    global _MODEL
    if _MODEL is None:
        from sentence_transformers import SentenceTransformer

        name = os.getenv("EMBEDDING_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
        _MODEL = SentenceTransformer(name, device=_configured_device())
    return _MODEL


def embed_query(text: str) -> list[float]:
    from pyvi import ViTokenizer

    model = _model()
    tokenized = ViTokenizer.tokenize(text)
    token_ids = model.tokenizer(tokenized, add_special_tokens=False)["input_ids"]
    if len(token_ids) > int(model.max_seq_length) - 2:
        raise ValueError(f"query exceeds model max_seq_length={model.max_seq_length}")
    dimensions = int(os.getenv("EMBEDDING_DIMENSIONS", str(model.get_embedding_dimension())))
    kwargs: dict[str, Any] = {
        "convert_to_numpy": True,
        "normalize_embeddings": True,
        "show_progress_bar": False,
    }
    if dimensions != int(model.get_embedding_dimension()):
        kwargs["truncate_dim"] = dimensions
    result = model.encode([tokenized], **kwargs)[0]
    values = result.tolist()
    if len(values) != dimensions:
        raise RuntimeError(f"embedding dimension mismatch: expected {dimensions}, got {len(values)}")
    return values


def model_name() -> str:
    return os.getenv("EMBEDDING_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
