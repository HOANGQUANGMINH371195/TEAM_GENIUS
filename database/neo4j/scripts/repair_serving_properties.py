#!/usr/bin/env python3
"""Backfill graph serving properties on an already imported release."""

from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv
from neo4j import GraphDatabase


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-id", required=True)
    args = parser.parse_args()
    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.getenv("NEO4J_USERNAME", "neo4j"), os.environ["NEO4J_PASSWORD"]),
    )
    database = os.getenv("NEO4J_DATABASE", "neo4j")
    with driver.session(database=database) as session:
        session.run(
            """
            MATCH (n:Document {dataset_id:$dataset_id})
            SET n.answer_ready = CASE WHEN n.node_kind = 'canonical_document' THEN true ELSE false END
            """,
            dataset_id=args.dataset_id,
        ).consume()
        session.run(
            """
            MATCH ()-[r]->()
            WHERE r.dataset_id=$dataset_id AND type(r) <> 'ALIAS_OF'
            SET r.source_start = CASE WHEN r.source_start IS NULL AND r.evidence_start <> ''
                                      THEN toInteger(r.evidence_start) ELSE r.source_start END,
                r.source_end = CASE WHEN r.source_end IS NULL AND r.evidence_end <> ''
                                    THEN toInteger(r.evidence_end) ELSE r.source_end END,
                r.official_url = coalesce(r.official_url, r.target_official_url, ''),
                r.checked_at = coalesce(r.checked_at, ''),
                r.release_fingerprint = coalesce(r.release_fingerprint, $dataset_id)
            """,
            dataset_id=args.dataset_id,
        ).consume()
    driver.close()
    print(f"Repaired serving properties for dataset {args.dataset_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
