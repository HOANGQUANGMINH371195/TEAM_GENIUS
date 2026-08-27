#!/usr/bin/env python3
"""Export reviewed PostgreSQL facts for a release-scoped Neo4j projection.

``legal_facts`` is the review boundary.  This command exports only accepted
rows and never attempts to recognize facts from document text.  The resulting
JSONL is intentionally compatible with ``import_typed_facts.py`` and can be
checked, hashed, and imported atomically as a separate deployment step.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import asyncpg
from dotenv import load_dotenv

_COLUMNS = (
    "fact_id", "subject", "predicate", "normalized_value", "effective_from",
    "effective_to", "jurisdiction", "provision_id", "document_id", "unit_id",
    "source_start", "source_end", "source_sha256", "review_status", "release_id",
)


def _database_url(value: str) -> str:
    url = value.strip()
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url.removeprefix("postgresql+asyncpg://")
    if url.startswith("postgresql://"):
        return url
    raise ValueError("database URL must use postgresql://")


def _json_value(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def serialize_fact_row(row: Any, *, release_id: str) -> dict[str, Any]:
    """Convert one asyncpg row without exposing unrelated database fields."""
    record = {column: _json_value(row[column]) for column in _COLUMNS if column != "release_id"}
    record["release_id"] = release_id
    record["review_status"] = "accepted"
    return record


async def export_facts(database_url: str, *, release_id: str) -> list[dict[str, Any]]:
    connection = await asyncpg.connect(_database_url(database_url))
    try:
        rows = await connection.fetch(
            """
            SELECT fact_id, subject, predicate, normalized_value, effective_from,
                   effective_to, jurisdiction, provision_id, document_id, unit_id,
                   source_start, source_end, source_sha256, review_status
            FROM public.legal_facts
            WHERE dataset_id = $1 AND review_status = 'accepted'
            ORDER BY fact_id
            """,
            release_id,
        )
        return [serialize_fact_row(row, release_id=release_id) for row in rows]
    finally:
        await connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database-url", default="")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    if not args.release_id.startswith("snapshot-"):
        raise SystemExit("release-id must be an immutable snapshot-... identifier")
    load_dotenv(args.env_file, override=False)
    database_url = args.database_url or os.getenv("RUNTIME_DATABASE_URL") or os.getenv("DATABASE_URL", "")
    if not database_url:
        raise SystemExit("DATABASE_URL or RUNTIME_DATABASE_URL is required")
    rows = asyncio.run(export_facts(database_url, release_id=args.release_id))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    print(json.dumps({"release_id": args.release_id, "accepted_facts": len(rows), "sha256": digest}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
