#!/usr/bin/env python3
"""Import reviewed, release-scoped typed facts into Neo4j.

The JSONL input is an offline projection exported from PostgreSQL's
``legal_facts`` table.  This command never recognizes facts from raw text and
never permits a release mix-up; recognition/review must happen upstream.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date
from pathlib import Path

from src.domain.facts import LegalFact
from src.integrations.neo4j import Neo4jGraphStore


def load_facts(path: Path, *, release_id: str) -> list[LegalFact]:
    facts: list[LegalFact] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            fact = LegalFact(
                fact_id=str(row["fact_id"]),
                subject=str(row["subject"]),
                predicate=str(row["predicate"]),
                normalized_value=str(row["normalized_value"]),
                effective_from=date.fromisoformat(row["effective_from"]) if row.get("effective_from") else None,
                effective_to=date.fromisoformat(row["effective_to"]) if row.get("effective_to") else None,
                jurisdiction=str(row.get("jurisdiction") or ""),
                provision_id=str(row.get("provision_id") or ""),
                document_id=str(row["document_id"]),
                unit_id=str(row["unit_id"]),
                source_start=int(row["source_start"]) if row.get("source_start") is not None else None,
                source_end=int(row["source_end"]) if row.get("source_end") is not None else None,
                source_sha256=str(row["source_sha256"]),
                review_status=str(row.get("review_status") or "pending"),
                release_id=str(row["release_id"]),
            )
            fact.validate()
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid fact at line {line_number}: {exc}") from exc
        if fact.release_id != release_id:
            raise ValueError(f"line {line_number}: release_id does not match {release_id}")
        if fact.review_status != "accepted":
            raise ValueError(f"line {line_number}: only accepted facts may be projected")
        if fact.fact_id in seen:
            raise ValueError(f"line {line_number}: duplicate fact_id {fact.fact_id}")
        seen.add(fact.fact_id)
        facts.append(fact)
    return facts


async def _import(path: Path, release_id: str) -> int:
    facts = load_facts(path, release_id=release_id)
    store = Neo4jGraphStore()
    try:
        return await store.upsert_legal_facts(facts)
    finally:
        await store.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    facts = load_facts(args.input, release_id=args.release_id)
    if args.dry_run:
        print(json.dumps({"release_id": args.release_id, "accepted_facts": len(facts)}))
        return 0
    count = asyncio.run(_import(args.input, args.release_id))
    print(json.dumps({"release_id": args.release_id, "projected_facts": count}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
