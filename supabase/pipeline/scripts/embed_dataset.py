#!/usr/bin/env python3
"""Embed one staged dataset into PostgreSQL/pgvector, then publish it.

Usage:
    python scripts/embed_dataset.py snapshot-0123abcd...

The active pointer is not changed until all non-empty passages have vectors.
If this command fails, readers continue to use the previous active release.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

import psycopg
import torch
from dotenv import load_dotenv
from pyvi import ViTokenizer
from sentence_transformers import SentenceTransformer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

load_dotenv()

from data_pipeline.storage import publish_dataset, ensure_dataset_vector_collection


DEFAULT_MODEL = "huyydangg/DEk21_hcmute_embedding_v2"
PREPROCESSOR = "pyvi.ViTokenizer"


def connection() -> psycopg.Connection:
    return psycopg.connect(
        host=os.getenv("PGHOST", "localhost"), port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "postgres"), user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", "postgres"),
    )


def vector_literal(values: object) -> str:
    return "[" + ",".join(format(float(value), ".10g") for value in values) + "]"


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def configured_device() -> str:
    device = os.getenv("EMBEDDING_DEVICE", "cuda:0").strip() or "cuda:0"
    if device.casefold().startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("EMBEDDING_DEVICE requests CUDA but CUDA is unavailable; set EMBEDDING_DEVICE=cpu explicitly")
    return device


def embedding_segments(model: SentenceTransformer, text: str) -> list[str]:
    """Validate that a prepared retrieval chunk fits the encoder.

    Chunks are created before ingestion. Refusing an oversized chunk here is
    safer than truncating it or hiding multiple legal provisions in a mean
    vector; the build must be rerun with a smaller target instead.
    """

    max_payload = int(model.max_seq_length) - 2  # reserve special tokens
    if max_payload <= 0:
        raise ValueError("embedding model has an invalid max_seq_length")
    tokenized_text = ViTokenizer.tokenize(text)
    token_ids = model.tokenizer(tokenized_text, add_special_tokens=False)["input_ids"]
    if len(token_ids) <= max_payload:
        return [tokenized_text]
    raise ValueError(
        f"prepared chunk exceeds model max_seq_length={model.max_seq_length}; "
        "rebuild the canonical release with a smaller chunk target"
    )


def embedding_input(model: SentenceTransformer, section_title: str, text: str) -> str:
    contextual = "\n\n".join(part for part in (section_title.strip(), text) if part)
    max_payload = int(model.max_seq_length) - 2
    contextual_ids = model.tokenizer(
        ViTokenizer.tokenize(contextual), add_special_tokens=False, truncation=False
    )["input_ids"]
    return contextual if len(contextual_ids) <= max_payload else text


def embed_dataset(dataset_id: str, *, batch_size: int) -> int:
    model_name = os.getenv("EMBEDDING_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    requested_dimensions = int(os.getenv("EMBEDDING_DIMENSIONS", "768"))
    if batch_size <= 0 or requested_dimensions <= 0:
        raise ValueError("batch size and embedding dimensions must be positive")
    model = SentenceTransformer(model_name, device=configured_device())
    base_dimensions = int(model.get_embedding_dimension())
    if requested_dimensions > base_dimensions:
        raise ValueError(f"EMBEDDING_DIMENSIONS={requested_dimensions} exceeds model dimension {base_dimensions}")
    encode_kwargs: dict[str, object] = {"convert_to_numpy": True, "normalize_embeddings": True, "show_progress_bar": False}
    if requested_dimensions != base_dimensions:
        encode_kwargs["truncate_dim"] = requested_dimensions

    with connection() as conn:
        ensure_dataset_vector_collection(conn, dataset_id, dimensions=requested_dimensions)
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM datasets WHERE dataset_id = %s", (dataset_id,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"Unknown dataset_id: {dataset_id}")
            if row[0] != "staging":
                raise ValueError(f"Release must be staging, found {row[0]!r}")
            cur.execute(
                """SELECT id, section_title, text FROM chunks
                   WHERE dataset_id = %s AND text <> '' AND embedding IS NULL
                   ORDER BY document_id, chunk_order""",
                (dataset_id,),
            )
            rows = cur.fetchall()
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            segmented_inputs = [
                embedding_segments(model, embedding_input(model, str(title), str(text)))
                for _, title, text in batch
            ]
            inputs = [segments[0] for segments in segmented_inputs]
            vectors = model.encode(inputs, batch_size=min(batch_size, len(inputs)), **encode_kwargs)
            with conn.cursor() as cur:
                for (source_key, _, _), segments, vector in zip(batch, segmented_inputs, vectors, strict=True):
                    input_text = "\n".join(segments)
                    input_hash = sha256(input_text)
                    cur.execute(
                        """UPDATE chunks
                           SET embedding_input_text = %s, embedding_input_sha256 = %s,
                               embedding = %s::extensions.vector, embedding_model = %s,
                               embedding_dimensions = %s, embedding_preprocessor = %s,
                               embedding_normalized = TRUE, embedded_input_sha256 = %s,
                               embedding_created_at = now()
                           WHERE dataset_id = %s AND id = %s""",
                        (input_text, input_hash, vector_literal(vector), model_name, requested_dimensions,
                         PREPROCESSOR, input_hash, dataset_id, source_key),
                    )
            conn.commit()
        # A release with no chunks cannot have reached staging validation; this
        # publishes only after the storage layer verifies every vector is set.
        publish_dataset(conn, dataset_id, require_embeddings=True)
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_id")
    parser.add_argument("--batch-size", type=int, default=int(os.getenv("EMBEDDING_BATCH_SIZE", "16")))
    args = parser.parse_args()
    print(f"Embedded and published {args.dataset_id}: {embed_dataset(args.dataset_id, batch_size=args.batch_size)} passages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
