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
from pathlib import Path

from src.domain.facts import LegalFact
from src.domain.ontology import ontology_issues
from src.integrations.neo4j import Neo4jGraphStore
from src.services.fact_recognizer import recognize_fact_rows


def load_facts(path: Path, *, release_id: str) -> list[LegalFact]:
    rows: list[dict] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("fact row must be an object")
            rows.append(row)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid fact at line {line_number}: {exc}") from exc
    for index, row in enumerate(rows, start=1):
        issues = ontology_issues(row)
        if issues:
            raise ValueError(f"invalid fact at line {index}: ontology issues: {', '.join(issues[:4])}")
    result = recognize_fact_rows(rows, release_id=release_id)
    if result.rejected:
        first = result.rejected[0]
        raise ValueError(f"invalid fact at line {int(first['row']) + 1}: {first['reason']}")
    facts = list(result.facts)
    unreviewed = next((index for index, fact in enumerate(facts) if fact.review_status != "accepted"), None)
    if unreviewed is not None:
        raise ValueError(f"line {unreviewed + 1}: only accepted facts may be projected")
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
