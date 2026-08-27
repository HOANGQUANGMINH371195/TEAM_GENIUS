#!/usr/bin/env python3
"""Verify active PostgreSQL/Neo4j metadata and embedding parity with a source."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import psycopg
from dotenv import load_dotenv
from neo4j import GraphDatabase
from qdrant_client import QdrantClient, models

load_dotenv()

PIPELINE_ROOT = Path(__file__).resolve().parents[1] / "pipeline"
if str(PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINE_ROOT))

from data_pipeline.canonical import build_snapshot  # noqa: E402


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def connection() -> psycopg.Connection[Any]:
    url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://", 1)
    return psycopg.connect(url, connect_timeout=20, application_name="live-corpus-parity")


def verify_external_embedding_artifact(
    root: Path, dataset_id: str, snapshot: Any
) -> dict[str, Any]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("dataset_id") != dataset_id:
        raise ValueError("external embedding artifact belongs to another dataset")
    vectors = np.load(root / "embeddings.float32.npy", mmap_mode="r")
    rows = int(manifest["rows"])
    dimensions = int(manifest["dimensions"])
    if vectors.shape != (rows, dimensions) or not np.isfinite(vectors).all():
        raise ValueError("external embedding matrix is incomplete or invalid")
    artifact_passages = {
        str(row["passage_id"]): str(row["input_sha256"])
        for row in (
            json.loads(line)
            for line in (root / "passages.jsonl").read_text(encoding="utf-8").splitlines()
        )
    }
    expected_passages = {}
    for row in snapshot.passages:
        if not bool(row.get("semantic_eligible", True)):
            continue
        text = "\n\n".join(
            part
            for part in (str(row.get("section_label", "")), str(row.get("text", "")))
            if part
        )
        expected_passages[str(row["passage_id"])] = hashlib.sha256(text.encode()).hexdigest()
    if artifact_passages != expected_passages or len(artifact_passages) != rows:
        raise ValueError("external embedding passage IDs/input hashes differ from snapshot")
    return {
        "storage": "external_local_artifact",
        "rows": rows,
        "dimensions": dimensions,
        "model": manifest.get("model", ""),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--external-embedding-artifact", type=Path,
        help="Accept absent in-database vector values only when this complete Qdrant-ready artifact matches.",
    )
    parser.add_argument(
        "--release-lock", type=Path,
        help="Optional tracked release lock containing source hashes, versions and expected counts.",
    )
    args = parser.parse_args()

    metadata = read_csv(args.source_dir / "metadata.csv")
    aliases = read_csv(args.source_dir / "aliases.csv")
    relationships = read_csv(args.source_dir / "relationships.csv")
    expected_documents = {row["id"]: row for row in metadata}
    expected_nodes = {
        **{identifier: (row["title"], row["so_ky_hieu"], "canonical_document")
           for identifier, row in expected_documents.items()},
        **{row["alias_document_id"]: (row["alias_title"], row["alias_signature"], "document_alias")
           for row in aliases},
    }
    expected_edges = {
        row["relationship_id"]: (row.get("source_title", ""), row.get("target_title", ""))
        for row in relationships
    }
    validation = json.loads((args.source_dir / "canonical_validation.json").read_text(encoding="utf-8"))
    snapshot = build_snapshot(args.source_dir)
    # A release ID is a content fingerprint, not a caller-provided label.  A
    # source directory that was rebuilt with a newer parser/chunker must fail
    # loudly instead of producing an opaque passage-ID mismatch later.  This
    # also catches stale ``canonical_validation.json`` files before any remote
    # store is inspected.
    errors: list[str] = []
    if str(validation.get("dataset_id", "")) != args.dataset_id:
        errors.append(
            "canonical validation dataset ID differs from requested release "
            f"({validation.get('dataset_id', '')!r} != {args.dataset_id!r})"
        )
    if str(snapshot.dataset_id) != args.dataset_id:
        errors.append(
            "source snapshot fingerprint differs from requested release "
            f"({snapshot.dataset_id!r} != {args.dataset_id!r})"
        )
    if args.release_lock:
        try:
            release_lock = json.loads(args.release_lock.read_text(encoding="utf-8"))
            if str(release_lock.get("release_id", "")) != args.dataset_id:
                errors.append("release lock belongs to another dataset")
            for filename, expected_hash in (release_lock.get("source_files_sha256") or {}).items():
                path = args.source_dir / str(filename)
                actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
                if actual_hash != str(expected_hash):
                    errors.append(f"release lock source hash mismatch: {filename}")
            locked_pipeline = release_lock.get("pipeline") or {}
            for lock_key, manifest_key in (
                ("pipeline_version", "pipeline_version"),
                ("normalizer_version", "normalizer_version"),
                ("passage_version", "passage_version"),
                ("legal_unit_version", "legal_unit_version"),
            ):
                if lock_key in locked_pipeline and str(snapshot.manifest.get(manifest_key, "")) != str(locked_pipeline[lock_key]):
                    errors.append(f"release lock {lock_key} mismatch")
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"release lock invalid: {exc}")
    validation_counts = validation.get("counts", {})
    snapshot_counts = {
        "documents": len(snapshot.documents),
        "aliases": len(snapshot.aliases),
        "relationships": len(snapshot.relationships),
        "passages": len(snapshot.passages),
        "semantic_passages": sum(bool(row.get("semantic_eligible")) for row in snapshot.passages),
        "legal_units": len(snapshot.legal_units),
    }
    for key, actual in snapshot_counts.items():
        expected = validation_counts.get(key)
        if expected is not None and int(expected) != actual:
            errors.append(
                f"canonical source artifact drift for {key}: "
                f"validation={int(expected)} rebuilt={actual}"
            )
    external_embeddings = None
    if args.external_embedding_artifact:
        try:
            external_embeddings = verify_external_embedding_artifact(
                args.external_embedding_artifact, args.dataset_id, snapshot
            )
        except (OSError, ValueError, KeyError) as exc:
            # Keep the parity report machine-readable and continue checking
            # PostgreSQL/Neo4j/Qdrant.  The report remains failed and gives the
            # operator the exact artifact mismatch rather than a traceback.
            errors.append(f"external embedding artifact invalid: {exc}")
    expected_chunks = {
        str(row["passage_id"]): (
            str(row["document_id"]),
            hashlib.sha256(str(row["text"]).encode()).hexdigest(),
            bool(row.get("semantic_eligible")),
        )
        for row in snapshot.passages
    }
    with connection() as db, db.cursor(row_factory=psycopg.rows.dict_row) as cursor:
        cursor.execute("SELECT active_dataset_id FROM dataset_state WHERE singleton")
        active = str(cursor.fetchone()["active_dataset_id"])
        if active != args.dataset_id:
            errors.append(f"PostgreSQL active dataset {active} != {args.dataset_id}")
        cursor.execute(
            """SELECT id, title, payload -> 'metadata' ->> 'so_ky_hieu' AS so_ky_hieu,
                      payload -> 'metadata' ->> 'tinh_trang_hieu_luc' AS legal_status
               FROM documents WHERE dataset_id=%s""", (args.dataset_id,),
        )
        postgres_rows = {str(row["id"]): row for row in cursor}
        if set(postgres_rows) != set(expected_documents):
            errors.append("PostgreSQL canonical document IDs differ from source")
        postgres_identity_mismatches = sorted(
            identifier for identifier, expected in expected_documents.items()
            if identifier in postgres_rows and (
                postgres_rows[identifier]["title"] != expected["title"]
                or (postgres_rows[identifier]["so_ky_hieu"] or "") != expected["so_ky_hieu"]
            )
        )
        if postgres_identity_mismatches:
            errors.append(f"PostgreSQL identity mismatches: {postgres_identity_mismatches[:20]}")
        cursor.execute(
            """SELECT EXISTS (
                       SELECT 1 FROM information_schema.columns
                       WHERE table_schema='public' AND table_name='chunks' AND column_name='embedding'
                   ) AS has_embedding"""
        )
        has_embedding = bool(cursor.fetchone()["has_embedding"])
        embedding_expression = "embedding IS NULL" if has_embedding else "TRUE"
        cursor.execute(
            f"""SELECT count(*) AS chunks,
                      count(*) FILTER (WHERE semantic_eligible) AS semantic_chunks,
                      count(*) FILTER (WHERE semantic_eligible AND {embedding_expression}) AS missing_embeddings
               FROM chunks WHERE dataset_id=%s""", (args.dataset_id,),
        )
        chunk_counts = dict(cursor.fetchone())
        embedded_expression = "embedding IS NOT NULL" if has_embedding else "FALSE"
        cursor.execute(
            f"""SELECT chunk_id, document_id, text, semantic_eligible, {embedded_expression} AS embedded
               FROM chunks WHERE dataset_id=%s""", (args.dataset_id,),
        )
        live_chunks = {
            str(row["chunk_id"]): (
                str(row["document_id"]),
                hashlib.sha256(str(row["text"]).encode()).hexdigest(),
                bool(row["semantic_eligible"]),
                bool(row["embedded"]),
            )
            for row in cursor
        }
        chunk_mismatches = sorted(
            identifier for identifier, expected in expected_chunks.items()
            if identifier in live_chunks and live_chunks[identifier][:3] != expected
        )
        if set(live_chunks) != set(expected_chunks):
            errors.append("PostgreSQL chunk IDs differ from canonical snapshot")
        if chunk_mismatches:
            errors.append(f"PostgreSQL chunk content/provenance mismatches: {chunk_mismatches[:20]}")
        cursor.execute("SELECT count(*) AS count FROM document_aliases WHERE dataset_id=%s", (args.dataset_id,))
        postgres_aliases = int(cursor.fetchone()["count"])
        cursor.execute(
            """SELECT projection_kind, locator, status, release_fingerprint,
                      expected_count, actual_count
               FROM release_projections WHERE dataset_id=%s""",
            (args.dataset_id,),
        )
        projection_rows = {str(row["projection_kind"]): row for row in cursor.fetchall()}
        if set(projection_rows) != {"postgres", "qdrant", "neo4j"}:
            errors.append("release projection registry is incomplete")
        for kind, row in projection_rows.items():
            if row["status"] != "ready" or row["release_fingerprint"] == "":
                errors.append(f"{kind} projection is not ready/fingerprint-scoped")
            if row["actual_count"] is not None and int(row["actual_count"]) != int(row["expected_count"]):
                errors.append(f"{kind} projection expected/actual counts differ")

    qdrant_point_count: int | None = None
    if os.getenv("QDRANT_URL") and os.getenv("QDRANT_API_KEY"):
        qdrant = QdrantClient(
            url=os.environ["QDRANT_URL"], api_key=os.environ["QDRANT_API_KEY"], timeout=30,
        )
        try:
            collection = os.getenv("QDRANT_COLLECTION", "medical_legal_active")
            if not qdrant.collection_exists(collection):
                errors.append(f"Qdrant collection missing: {collection}")
            else:
                qdrant_point_count = int(qdrant.count(
                    collection,
                    count_filter=models.Filter(must=[
                        models.FieldCondition(key="dataset_id", match=models.MatchValue(value=args.dataset_id))
                    ]),
                    exact=True,
                ).count)
                qdrant_row = projection_rows.get("qdrant")
                if qdrant_row is None or qdrant_point_count != int(qdrant_row["expected_count"]):
                    errors.append(
                        f"Qdrant point count {qdrant_point_count} does not match registry "
                        f"{qdrant_row['expected_count'] if qdrant_row else 'missing'}"
                    )
        finally:
            qdrant.close()

    neo4j_dataset_counts: dict[str, int] = {}
    warnings: list[str] = []
    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.getenv("NEO4J_USERNAME", "neo4j"), os.environ["NEO4J_PASSWORD"]),
    )
    try:
        with driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as session:
            neo4j_dataset_counts = {
                str(row["dataset"]): int(row["count"])
                for row in session.run(
                    """MATCH (n:Document)
                       WHERE n.dataset_id IS NOT NULL
                       RETURN n.dataset_id AS dataset, count(n) AS count
                       ORDER BY dataset"""
                ).data()
            }
            stale_datasets = sorted(set(neo4j_dataset_counts) - {args.dataset_id})
            if stale_datasets:
                warnings.append(
                    "Neo4j contains release-scoped datasets outside the requested "
                    f"release: {stale_datasets}"
                )
            node_rows = session.run(
                """MATCH (n:Document {dataset_id:$dataset_id})
                   WHERE n.node_kind IN ['canonical_document', 'document_alias']
                   RETURN n.id AS id, n.title AS title, n.so_ky_hieu AS so_ky_hieu,
                          n.node_kind AS node_kind, n.legal_status AS legal_status""",
                dataset_id=args.dataset_id,
            ).data()
            actual_nodes = {str(row["id"]): row for row in node_rows}
            if set(actual_nodes) != set(expected_nodes):
                errors.append("Neo4j document/alias IDs differ from source")
            neo4j_identity_mismatches = sorted(
                identifier for identifier, expected in expected_nodes.items()
                if identifier in actual_nodes and (
                    actual_nodes[identifier].get("title") != expected[0]
                    or (actual_nodes[identifier].get("so_ky_hieu") or "") != expected[1]
                    or actual_nodes[identifier].get("node_kind") != expected[2]
                )
            )
            if neo4j_identity_mismatches:
                errors.append(f"Neo4j identity mismatches: {neo4j_identity_mismatches[:20]}")
            edge_rows = session.run(
                """MATCH ()-[r]->() WHERE r.dataset_id=$dataset_id AND type(r) <> 'ALIAS_OF'
                   RETURN r.relationship_id AS relationship_id, r.source_title AS source_title,
                          r.target_title AS target_title""",
                dataset_id=args.dataset_id,
            ).data()
            actual_edges = {str(row["relationship_id"]): row for row in edge_rows}
            if set(actual_edges) != set(expected_edges):
                errors.append("Neo4j legal relationship IDs differ from source")
            edge_title_mismatches = sorted(
                identifier for identifier, expected in expected_edges.items()
                if identifier in actual_edges and (
                    (actual_edges[identifier].get("source_title") or "") != expected[0]
                    or (actual_edges[identifier].get("target_title") or "") != expected[1]
                )
            )
            if edge_title_mismatches:
                errors.append(f"Neo4j relationship title mismatches: {edge_title_mismatches[:20]}")
            alias_edges = int(session.run(
                "MATCH ()-[r:ALIAS_OF]->() WHERE r.dataset_id=$dataset_id RETURN count(r) AS count",
                dataset_id=args.dataset_id,
            ).single()["count"])
            reference_nodes = int(session.run(
                """MATCH (n:Document {dataset_id:$dataset_id, node_kind:'reference_only'})
                   RETURN count(n) AS count""",
                dataset_id=args.dataset_id,
            ).single()["count"])
            approved_edges = int(session.run(
                "MATCH ()-[r]->() WHERE r.dataset_id=$dataset_id AND r.serving_status='approved_evidence' RETURN count(r) AS count",
                dataset_id=args.dataset_id,
            ).single()["count"])
    finally:
        driver.close()

    expected_passages = int(validation["counts"]["passages"])
    expected_semantic = int(validation["counts"]["semantic_passages"])
    if int(chunk_counts["chunks"]) != expected_passages:
        errors.append(f"PostgreSQL chunk count {chunk_counts['chunks']} != {expected_passages}")
    if int(chunk_counts["semantic_chunks"]) != expected_semantic:
        errors.append(f"PostgreSQL semantic chunk count {chunk_counts['semantic_chunks']} != {expected_semantic}")
    if int(chunk_counts["missing_embeddings"]) and external_embeddings is None:
        errors.append(f"missing semantic embeddings: {chunk_counts['missing_embeddings']}")
    if external_embeddings is not None and int(chunk_counts["missing_embeddings"]) != expected_semantic:
        errors.append("external-vector mode requires all semantic vectors to be offloaded from PostgreSQL")
    if postgres_aliases != len(aliases) or alias_edges != len(aliases):
        errors.append("alias count differs between source and live stores")
    expected_references = int(validation["counts"]["relationship_reference_only_endpoints"])
    if reference_nodes != expected_references:
        errors.append(f"Neo4j reference node count {reference_nodes} != {expected_references}")
    for identifier, expected_status in (("58187", "Còn hiệu lực"), ("37927", "Còn hiệu lực"), ("46986", "Còn hiệu lực")):
        if postgres_rows[identifier]["legal_status"] != expected_status:
            errors.append(f"PostgreSQL {identifier} legal status is not {expected_status}")
        if actual_nodes[identifier]["legal_status"] != expected_status:
            errors.append(f"Neo4j {identifier} legal status is not {expected_status}")

    report = {
        "status": "pass" if not errors else "fail",
        "dataset_id": args.dataset_id,
        "errors": errors,
        "warnings": warnings,
        "source_snapshot": {
            "rebuilt_dataset_id": str(snapshot.dataset_id),
            "validation_dataset_id": str(validation.get("dataset_id", "")),
            "rebuilt_counts": snapshot_counts,
            "validation_counts": {
                key: validation_counts.get(key)
                for key in snapshot_counts
                if key in validation_counts
            },
        },
        "external_embeddings": external_embeddings,
        "counts": {
            "postgres_documents": len(postgres_rows),
            "postgres_aliases": postgres_aliases,
            "release_projections": {
                kind: {
                    "locator": str(row["locator"]),
                    "status": str(row["status"]),
                    "expected_count": int(row["expected_count"]),
                    "actual_count": int(row["actual_count"]) if row["actual_count"] is not None else None,
                }
                for kind, row in projection_rows.items()
            },
            "qdrant_actual_points": qdrant_point_count,
            "chunks": int(chunk_counts["chunks"]),
            "semantic_chunks": int(chunk_counts["semantic_chunks"]),
            "missing_semantic_embeddings": int(chunk_counts["missing_embeddings"]),
            "chunk_content_mismatches": len(chunk_mismatches),
            "neo4j_document_nodes": len(actual_nodes),
            "neo4j_dataset_nodes": neo4j_dataset_counts,
            "neo4j_reference_nodes": reference_nodes,
            "neo4j_approved_evidence": approved_edges,
            "neo4j_legal_relationships": len(actual_edges),
            "neo4j_alias_relationships": alias_edges,
            "postgres_identity_mismatches": len(postgres_identity_mismatches),
            "neo4j_identity_mismatches": len(neo4j_identity_mismatches),
            "neo4j_relationship_title_mismatches": len(edge_title_mismatches),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
