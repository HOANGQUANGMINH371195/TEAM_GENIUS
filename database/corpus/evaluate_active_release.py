#!/usr/bin/env python3
"""Evaluate active exact/semantic retrieval and Neo4j evidence parity."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from neo4j import GraphDatabase

PIPELINE_ROOT = Path(__file__).resolve().parents[1] / "pipeline"
sys.path.insert(0, str(PIPELINE_ROOT))
load_dotenv()

from data_pipeline.api_repository import PsycopgReadRepository  # noqa: E402
from data_pipeline.embedding import embed_batch  # noqa: E402


def connection() -> psycopg.Connection:
    return psycopg.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "postgres"),
        user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", "postgres"),
    )


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(format(value, ".10g") for value in values) + "]"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--semantic-limit", type=int, default=10)
    args = parser.parse_args()
    cases = [json.loads(line) for line in args.benchmark.read_text(encoding="utf-8").splitlines() if line]
    exact_cases = [case for case in cases if case["case_type"] == "exact_document_retrieval"]
    graph_cases = [case for case in cases if case["case_type"] != "exact_document_retrieval"]

    repository = PsycopgReadRepository(connection_factory=connection)
    exact_pass = 0
    exact_failures: list[str] = []
    for case in exact_cases:
        result = repository.exact_search(case["expected_signature"], category=None, status=None, limit=10)
        ids = {hit.document_id for hit in result.hits} if result else set()
        expected = set(case["expected_document_ids"])
        if expected & ids:
            exact_pass += 1
        else:
            exact_failures.append(case["case_id"])

    vectors = embed_batch([case["query"] for case in exact_cases])
    semantic_at_5 = semantic_at_10 = 0
    semantic_failures: list[str] = []
    with connection() as conn, conn.cursor() as cur:
        for case, vector in zip(exact_cases, vectors, strict=True):
            literal = vector_literal(vector)
            cur.execute(
                """SELECT document_id FROM active_graph_chunks
                   WHERE embedding IS NOT NULL AND semantic_eligible
                   ORDER BY embedding <=> %s::extensions.vector, chunk_id
                   LIMIT %s""",
                (literal, args.semantic_limit),
            )
            hits = [row[0] for row in cur.fetchall()]
            expected = set(case["expected_document_ids"])
            if expected & set(hits[:5]):
                semantic_at_5 += 1
            if expected & set(hits[:10]):
                semantic_at_10 += 1
            else:
                semantic_failures.append(case["case_id"])

    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.getenv("NEO4J_USERNAME", "neo4j"), os.environ["NEO4J_PASSWORD"]),
    )
    dataset_id = repository.current_dataset().dataset_id
    graph_pass = 0
    graph_failures: list[str] = []
    with driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as session:
        for case in graph_cases:
            record = session.run(
                """MATCH ()-[r]->() WHERE r.dataset_id=$dataset_id
                   AND r.relationship_id=$relationship_id
                   RETURN r.evidence_sha256 AS evidence_sha256,
                          r.relationship_type AS relationship_type,
                          r.scope AS scope""",
                dataset_id=dataset_id,
                relationship_id=case["expected_relationship_id"],
            ).single()
            passed = bool(record) and (
                not case.get("expected_evidence_sha256")
                or record["evidence_sha256"] == case["expected_evidence_sha256"]
            )
            if passed:
                graph_pass += 1
            else:
                graph_failures.append(case["case_id"])
    driver.close()

    report = {
        "dataset_id": dataset_id,
        "benchmark_cases": len(cases),
        "exact_identifier": {
            "passed": exact_pass,
            "total": len(exact_cases),
            "recall_at_10": exact_pass / len(exact_cases),
        },
        "semantic": {
            "recall_at_5": semantic_at_5 / len(exact_cases),
            "recall_at_10": semantic_at_10 / len(exact_cases),
            "passed_at_10": semantic_at_10,
            "total": len(exact_cases),
        },
        "graph_evidence": {"passed": graph_pass, "total": len(graph_cases), "parity": graph_pass / len(graph_cases)},
        "failures": {"exact": exact_failures, "semantic_at_10": semantic_failures, "graph": graph_failures},
        "release_gate_pass": exact_pass == len(exact_cases) and graph_pass == len(graph_cases) and semantic_at_10 >= 80,
        "human_adjudicated": False,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["release_gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
