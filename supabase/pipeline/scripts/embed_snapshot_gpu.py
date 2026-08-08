#!/usr/bin/env python3
"""Encode a canonical snapshot on the local CUDA GPU without a database.

This produces a release-scoped vector artifact that can be inspected or loaded
by the Supabase staging worker.  It never mutates a database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np
import torch
from pyvi import ViTokenizer
from sentence_transformers import SentenceTransformer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_pipeline.canonical import build_snapshot


DEFAULT_MODEL = "huyydangg/DEk21_hcmute_embedding_v2"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _input_text(row: dict[str, object], model: SentenceTransformer, max_payload: int) -> str:
    title = str(row.get("section_label") or "").strip()
    text = str(row.get("text") or "")
    contextual = "\n\n".join(part for part in (title, text) if part)
    if len(model.tokenizer(ViTokenizer.tokenize(contextual), add_special_tokens=False, truncation=False)["input_ids"]) <= max_payload:
        return contextual
    # A very long legal heading must not make an otherwise valid passage
    # exceed the encoder limit; the passage text remains the authoritative
    # semantic input and retains its unit_id/source span for citation.
    return text


def encode_snapshot(source_dir: str | Path, output_root: str | Path, *, batch_size: int) -> Path:
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable; this command requires the real local GPU")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    snapshot = build_snapshot(source_dir)
    model_name = os.getenv("EMBEDDING_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    dimensions = int(os.getenv("EMBEDDING_DIMENSIONS", "768"))
    model = SentenceTransformer(model_name, device="cuda:0")
    base_dimensions = int(model.get_embedding_dimension())
    if dimensions > base_dimensions:
        raise ValueError(f"EMBEDDING_DIMENSIONS={dimensions} exceeds model dimension {base_dimensions}")
    max_payload = int(model.max_seq_length) - 2
    inputs = [_input_text(row, model, max_payload) for row in snapshot.passages]
    for index, text in enumerate(inputs):
        tokens = model.tokenizer(
            ViTokenizer.tokenize(text), add_special_tokens=False, truncation=False
        )["input_ids"]
        if len(tokens) > max_payload:
            raise ValueError(
                f"passage {snapshot.passages[index]['passage_id']} has {len(tokens)} tokens; "
                f"model payload limit is {max_payload}"
            )

    kwargs: dict[str, object] = {
        "batch_size": batch_size,
        "convert_to_numpy": True,
        "normalize_embeddings": True,
        "show_progress_bar": True,
    }
    if dimensions != base_dimensions:
        kwargs["truncate_dim"] = dimensions
    vectors = model.encode(inputs, **kwargs).astype(np.float32, copy=False)

    target = Path(output_root) / snapshot.dataset_id
    target.mkdir(parents=True, exist_ok=True)
    np.save(target / "embeddings.float32.npy", vectors)
    with (target / "passages.jsonl").open("w", encoding="utf-8") as handle:
        for row, text in zip(snapshot.passages, inputs, strict=True):
            handle.write(json.dumps({
                "passage_id": row["passage_id"],
                "document_id": row["document_id"],
                "unit_id": row["unit_id"],
                "source_start": row["source_start"],
                "source_end": row["source_end"],
                "input_sha256": _sha256(text),
            }, ensure_ascii=False, sort_keys=True) + "\n")
    manifest = {
        "artifact_type": "gpu_embedding_snapshot",
        "dataset_id": snapshot.dataset_id,
        "source_manifest_sha256": snapshot.manifest["source_manifest_sha256"],
        "model": model_name,
        "device": "cuda:0",
        "gpu": torch.cuda.get_device_name(0),
        "dimensions": int(vectors.shape[1]),
        "rows": int(vectors.shape[0]),
        "normalized": True,
        "max_model_payload_tokens": max_payload,
        "files": {"vectors": "embeddings.float32.npy", "passages": "passages.jsonl"},
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", default="data/raw")
    parser.add_argument("--output-dir", default="data/clean/embeddings")
    parser.add_argument("--batch-size", type=int, default=int(os.getenv("EMBEDDING_BATCH_SIZE", "8")))
    args = parser.parse_args()
    target = encode_snapshot(args.source_dir, args.output_dir, batch_size=args.batch_size)
    print(f"GPU embeddings written to {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
