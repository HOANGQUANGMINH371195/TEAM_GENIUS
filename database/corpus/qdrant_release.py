#!/usr/bin/env python3
"""Publish a verified local embedding artifact to a versioned Qdrant release.

PostgreSQL remains the canonical store for document text and provenance.  This
tool only creates a derived Qdrant index, verifies it against the immutable
artifact, then (optionally) moves a stable alias in one atomic operation.
It deliberately never deletes a collection or overwrites an alias before the
new collection has passed parity.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from dotenv import load_dotenv
from qdrant_client import QdrantClient, models

PAYLOAD_INDEXES: tuple[tuple[str, models.PayloadSchemaType], ...] = (
    ("dataset_id", models.PayloadSchemaType.KEYWORD),
    ("document_id", models.PayloadSchemaType.KEYWORD),
    ("answer_ready", models.PayloadSchemaType.BOOL),
    ("retrieval_scope", models.PayloadSchemaType.KEYWORD),
    ("legal_status", models.PayloadSchemaType.KEYWORD),
    ("categories", models.PayloadSchemaType.KEYWORD),
)
DENSE_VECTOR_NAME = "dense"
BM25_VECTOR_NAME = "bm25"


@dataclass(frozen=True)
class Artifact:
    root: Path
    dataset_id: str
    model: str
    dimensions: int
    rows: int
    normalized: bool
    vectors: np.ndarray
    passages: list[dict[str, Any]]


def collection_name_for(dataset_id: str) -> str:
    """Create a Qdrant-safe physical collection name from a release ID."""
    return "medical_legal_hybrid_" + "".join(
        character if character.isalnum() or character in "_-" else "_"
        for character in dataset_id.casefold()
    )


def load_artifact(root: Path, expected_dataset_id: str | None = None) -> Artifact:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    dataset_id = str(manifest.get("dataset_id") or manifest.get("release_id") or "")
    if not dataset_id:
        raise ValueError("embedding artifact has no dataset_id")
    if expected_dataset_id and dataset_id != expected_dataset_id:
        raise ValueError("embedding artifact belongs to a different dataset")
    vectors = np.load(root / "embeddings.float32.npy", mmap_mode="r")
    passages = [
        json.loads(line)
        for line in (root / "passages.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rows = int(manifest["rows"])
    dimensions = int(manifest["dimensions"])
    if vectors.shape != (rows, dimensions) or len(passages) != rows:
        raise ValueError("artifact vector/metadata dimensions do not match manifest")
    if not np.isfinite(vectors).all():
        raise ValueError("artifact contains non-finite vector values")
    passage_ids = [str(row.get("passage_id", "")) for row in passages]
    if len(set(passage_ids)) != rows or any(len(identifier) != 32 for identifier in passage_ids):
        raise ValueError("artifact passage IDs must be unique 32-character Qdrant UUIDs")
    hashes = [str(row.get("input_sha256", "")) for row in passages]
    if any(len(value) != 64 for value in hashes):
        raise ValueError("artifact contains an invalid input_sha256")
    return Artifact(
        root=root,
        dataset_id=dataset_id,
        model=str(manifest.get("model", "unknown")),
        dimensions=dimensions,
        rows=rows,
        normalized=bool(manifest.get("normalized", False)),
        vectors=vectors,
        passages=passages,
    )


def _as_bool(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes"}


def load_document_payloads(
    metadata_path: Path,
    dataset_id: str,
    *,
    official_instrument_dir: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Return only non-content metadata that is safe and useful for filtering."""
    result: dict[str, dict[str, Any]] = {}
    with metadata_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            document_id = str(row.get("id", ""))
            if not document_id:
                continue
            categories = [
                value.strip() for value in (
                    row.get("agent_category", ""), row.get("nganh", ""), row.get("linh_vuc", "")
                ) if value and value.strip()
            ]
            result[document_id] = {
                "dataset_id": dataset_id,
                "dataset_version": dataset_id,
                "answer_ready": _as_bool(row.get("answer_ready")),
                "retrieval_scope": str(row.get("retrieval_scope", "")),
                "legal_status": str(row.get("tinh_trang_hieu_luc", "")),
                "categories": categories,
            }
    if official_instrument_dir is not None:
        if not official_instrument_dir.is_dir():
            raise ValueError(f"official instrument directory is missing: {official_instrument_dir}")
        for path in sorted(official_instrument_dir.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            metadata = payload.get("metadata") if isinstance(payload, dict) else None
            if not isinstance(metadata, dict):
                raise ValueError(f"official instrument {path.name} lacks metadata")
            document_id = str(metadata.get("id") or "")
            if not document_id or document_id in result:
                raise ValueError(f"official instrument has duplicate/missing id: {path.name}")
            categories = [
                value.strip()
                for value in str(metadata.get("agent_category") or "").split(",")
                if value.strip()
            ]
            result[document_id] = {
                "dataset_id": dataset_id,
                "dataset_version": dataset_id,
                "answer_ready": _as_bool(str(metadata.get("answer_ready") or "")),
                "retrieval_scope": str(metadata.get("retrieval_scope") or "official_overlay"),
                "legal_status": str(metadata.get("tinh_trang_hieu_luc") or ""),
                "categories": categories,
            }
    if not result:
        raise ValueError("metadata.csv did not contain any documents")
    return result


def _lexical_texts(artifact: Artifact, lexical_inputs: Path | None = None) -> dict[str, str]:
    """Read immutable lexical inputs embedded in a modern artifact.

    Sparse BM25 must use the exact canonical passage input, not a title or a
    lossy re-hydrated snippet.  Older artifacts intentionally fail closed and
    can only be published with ``--dense-only`` or rebuilt once.
    """
    texts = {
        str(passage["passage_id"]): str(passage.get("lexical_text") or "").strip()
        for passage in artifact.passages
    }
    if lexical_inputs is not None:
        supplied = {
            str(row.get("passage_id") or ""): str(row.get("lexical_text") or "").strip()
            for row in (
                json.loads(line) for line in lexical_inputs.read_text(encoding="utf-8").splitlines() if line.strip()
            )
        }
        for passage_id in texts:
            if supplied.get(passage_id):
                texts[passage_id] = supplied[passage_id]
    missing = [identifier for identifier, value in texts.items() if not value]
    if missing:
        raise ValueError(
            "artifact has no canonical lexical_text for BM25; rebuild the embedding artifact "
            "with the current pipeline or use --dense-only explicitly"
        )
    return texts


def build_points(
    artifact: Artifact,
    document_payloads: dict[str, dict[str, Any]],
    *,
    hybrid_bm25: bool,
    lexical_inputs: Path | None = None,
) -> Iterable[models.PointStruct]:
    lexical_texts = _lexical_texts(artifact, lexical_inputs) if hybrid_bm25 else {}
    for passage, vector in zip(artifact.passages, artifact.vectors, strict=True):
        document_id = str(passage["document_id"])
        document_payload = document_payloads.get(document_id)
        if document_payload is None:
            raise ValueError(f"passage {passage['passage_id']} references unknown document {document_id}")
        payload = {
            **document_payload,
            "passage_id": str(passage["passage_id"]),
            "document_id": document_id,
            "unit_id": str(passage.get("unit_id") or ""),
            "source_start": int(passage.get("source_start") or 0),
            "source_end": int(passage.get("source_end") or 0),
            "input_sha256": str(passage["input_sha256"]),
            "embedding_model": artifact.model,
            "embedding_dimensions": artifact.dimensions,
        }
        vector: list[float] | dict[str, object] = np.asarray(vector, dtype=np.float32).tolist()
        if hybrid_bm25:
            vector = {
                DENSE_VECTOR_NAME: vector,
                BM25_VECTOR_NAME: models.Document(
                    text=lexical_texts[str(passage["passage_id"])], model="qdrant/bm25"
                ),
            }
        yield models.PointStruct(
            id=str(passage["passage_id"]),
            vector=vector,
            payload=payload,
        )


def _assert_collection_shape(
    client: QdrantClient, collection: str, dimensions: int, *, hybrid_bm25: bool
) -> None:
    info = client.get_collection(collection)
    vectors = info.config.params.vectors
    dense = vectors.get(DENSE_VECTOR_NAME) if isinstance(vectors, dict) else vectors
    if not isinstance(dense, models.VectorParams) or dense.size != dimensions:
        raise ValueError(f"Qdrant collection {collection} has incompatible vector dimensions")
    if dense.distance != models.Distance.COSINE:
        raise ValueError(f"Qdrant collection {collection} must use cosine distance")
    sparse = info.config.params.sparse_vectors or {}
    if hybrid_bm25 and BM25_VECTOR_NAME not in sparse:
        raise ValueError(f"Qdrant collection {collection} has no {BM25_VECTOR_NAME} sparse vector")


def ensure_collection(
    client: QdrantClient, collection: str, artifact: Artifact, *, hybrid_bm25: bool
) -> None:
    if not client.collection_exists(collection):
        vectors_config: models.VectorParams | dict[str, models.VectorParams]
        vectors_config = (
            {DENSE_VECTOR_NAME: models.VectorParams(size=artifact.dimensions, distance=models.Distance.COSINE)}
            if hybrid_bm25
            else models.VectorParams(size=artifact.dimensions, distance=models.Distance.COSINE)
        )
        client.create_collection(
            collection_name=collection,
            vectors_config=vectors_config,
            sparse_vectors_config=(
                {BM25_VECTOR_NAME: models.SparseVectorParams(modifier=models.Modifier.IDF)}
                if hybrid_bm25
                else None
            ),
            metadata={
                "dataset_id": artifact.dataset_id,
                "embedding_model": artifact.model,
                "embedding_dimensions": artifact.dimensions,
                "artifact_rows": artifact.rows,
                "retrieval_mode": "hybrid_dense_bm25" if hybrid_bm25 else "dense_only",
            },
            timeout=60,
        )
    _assert_collection_shape(client, collection, artifact.dimensions, hybrid_bm25=hybrid_bm25)
    for field_name, field_type in PAYLOAD_INDEXES:
        client.create_payload_index(collection, field_name, field_type, wait=True, timeout=60)


def upload_points(client: QdrantClient, collection: str, points: Iterable[models.PointStruct], *, batch_size: int) -> int:
    batches: list[list[models.PointStruct]] = []
    batch: list[models.PointStruct] = []
    for point in points:
        batch.append(point)
        if len(batch) >= batch_size:
            batches.append(batch)
            batch = []
    if batch:
        batches.append(batch)
    # Cloud requests dominate CPU time.  A small bounded fan-out keeps each
    # Qdrant write fully acknowledged without making a one-off release upload
    # needlessly serial.  Point IDs make retries idempotent.
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [
            executor.submit(client.upsert, collection, points=part, wait=True, timeout=120)
            for part in batches
        ]
        for future in futures:
            future.result()
    return sum(len(part) for part in batches)


def verify_parity(
    client: QdrantClient, collection: str, artifact: Artifact, *, hybrid_bm25: bool, sample_size: int = 64
) -> dict[str, Any]:
    """Verify every ID/hash plus a deterministic sample of returned vectors."""
    expected = {str(row["passage_id"]): str(row["input_sha256"]) for row in artifact.passages}
    actual: dict[str, str] = {}
    batches = [list(expected)[start : start + 256] for start in range(0, len(expected), 256)]

    def retrieve_batch(identifiers: list[str]):
        return client.retrieve(
            collection, ids=identifiers,
            with_payload=["passage_id", "dataset_id", "input_sha256", "embedding_dimensions"],
            with_vectors=False, timeout=120,
        )

    with ThreadPoolExecutor(max_workers=6) as executor:
        for points in executor.map(retrieve_batch, batches):
            for point in points:
                payload = point.payload or {}
                point_id = str(point.id).replace("-", "")
                if payload.get("dataset_id") != artifact.dataset_id:
                    raise ValueError(f"Qdrant point {point_id} belongs to another dataset")
                if int(payload.get("embedding_dimensions", 0)) != artifact.dimensions:
                    raise ValueError(f"Qdrant point {point_id} has an unexpected dimension")
                actual[point_id] = str(payload.get("input_sha256", ""))
    if actual != expected:
        missing = sorted(set(expected) - set(actual))[:10]
        unexpected = sorted(set(actual) - set(expected))[:10]
        changed = sorted(identifier for identifier in set(expected) & set(actual) if expected[identifier] != actual[identifier])[:10]
        raise ValueError(f"Qdrant parity mismatch missing={missing} unexpected={unexpected} changed_hash={changed}")
    rng = random.Random(artifact.dataset_id)
    sample_indexes = sorted(rng.sample(range(artifact.rows), k=min(sample_size, artifact.rows)))
    records = client.retrieve(
        collection,
        ids=[str(artifact.passages[index]["passage_id"]) for index in sample_indexes],
        with_payload=False,
        with_vectors=True,
        timeout=120,
    )
    vectors_by_id = {
        str(record.id).replace("-", ""): np.asarray(
            record.vector[DENSE_VECTOR_NAME] if hybrid_bm25 and isinstance(record.vector, dict) else record.vector,
            dtype=np.float32,
        )
        for record in records
    }
    for index in sample_indexes:
        identifier = str(artifact.passages[index]["passage_id"])
        actual_vector = vectors_by_id.get(identifier)
        expected_vector = np.asarray(artifact.vectors[index], dtype=np.float32)
        if actual_vector is None or actual_vector.shape != expected_vector.shape:
            raise ValueError(f"Qdrant vector sample mismatch for {identifier}")
        denominator = float(np.linalg.norm(actual_vector) * np.linalg.norm(expected_vector))
        cosine = float(np.dot(actual_vector, expected_vector) / denominator) if denominator else 0.0
        # Cosine collections normalize vectors during ingestion, so bytewise
        # equality is neither expected nor useful.  Direction parity is the
        # release contract.
        if cosine < 0.999999:
            raise ValueError(f"Qdrant vector cosine mismatch for {identifier}: {cosine:.8f}")
    return {"point_count": len(actual), "id_hash_parity": True, "vector_samples_checked": len(sample_indexes)}


def activate_alias(client: QdrantClient, alias: str, collection: str) -> None:
    existing = {item.alias_name: item.collection_name for item in client.get_aliases().aliases}
    if existing.get(alias) == collection:
        return
    changes: list[models.CreateAliasOperation | models.DeleteAliasOperation] = []
    if alias in existing:
        changes.append(models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=alias)))
    changes.append(models.CreateAliasOperation(create_alias=models.CreateAlias(collection_name=collection, alias_name=alias)))
    client.update_collection_aliases(changes, timeout=60)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--metadata-csv", type=Path, required=True)
    parser.add_argument(
        "--official-instrument-dir", type=Path,
        help="Optional provenance-locked official provision overlays included in the artifact.",
    )
    parser.add_argument("--dataset-id")
    parser.add_argument("--collection")
    parser.add_argument("--alias", default=os.getenv("QDRANT_COLLECTION", "medical_legal_active"))
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--activate", action="store_true", help="Move the stable alias after all parity checks pass.")
    parser.add_argument("--verify-only", action="store_true", help="Verify an existing physical collection without sending upserts.")
    parser.add_argument("--dense-only", action="store_true", help="Publish only dense vectors; this disables BM25 hybrid retrieval.")
    parser.add_argument(
        "--lexical-inputs", type=Path,
        help="JSONL canonical passage_id/lexical_text export, required to retrofit BM25 to an older artifact.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not 64 <= args.batch_size <= 256:
        raise ValueError("batch-size must be between 64 and 256")
    load_dotenv()
    if not os.getenv("QDRANT_URL") or not os.getenv("QDRANT_API_KEY"):
        raise RuntimeError("QDRANT_URL and QDRANT_API_KEY are required")
    artifact = load_artifact(args.artifact_dir, args.dataset_id)
    hybrid_bm25 = not args.dense_only
    collection = args.collection or collection_name_for(artifact.dataset_id)
    document_payloads = load_document_payloads(
        args.metadata_csv,
        artifact.dataset_id,
        official_instrument_dir=args.official_instrument_dir,
    )
    report: dict[str, Any] = {
        "dataset_id": artifact.dataset_id,
        "collection": collection,
        "alias": args.alias,
        "artifact_rows": artifact.rows,
        "dimensions": artifact.dimensions,
        "model": artifact.model,
        "hybrid_bm25": hybrid_bm25,
        "dry_run": args.dry_run,
    }
    if args.dry_run:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    client = QdrantClient(
        url=os.environ["QDRANT_URL"],
        api_key=os.environ["QDRANT_API_KEY"],
        timeout=120,
        # Materialize qdrant/bm25 in the managed cluster, never through a
        # local FastEmbed model. This makes upload thread-safe and matches the
        # Render request-time execution path.
        cloud_inference=True,
    )
    if args.verify_only:
        if not client.collection_exists(collection):
            raise ValueError(f"Qdrant collection {collection} does not exist")
        _assert_collection_shape(client, collection, artifact.dimensions, hybrid_bm25=hybrid_bm25)
    else:
        ensure_collection(client, collection, artifact, hybrid_bm25=hybrid_bm25)
        report["uploaded_or_replaced_points"] = upload_points(
            client,
            collection,
            build_points(
                artifact,
                document_payloads,
                hybrid_bm25=hybrid_bm25,
                lexical_inputs=args.lexical_inputs,
            ),
            batch_size=args.batch_size,
        )
    report["parity"] = verify_parity(client, collection, artifact, hybrid_bm25=hybrid_bm25)
    if args.activate:
        activate_alias(client, args.alias, collection)
        report["alias_activated"] = True
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
