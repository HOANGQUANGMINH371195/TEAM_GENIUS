#!/usr/bin/env python3
"""Export canonical passage text for a Qdrant BM25 release.

This reads the active PostgreSQL source of truth and emits only the immutable
passage ID plus its canonical searchable text.  The output is an operational
artifact: do not commit it, because Qdrant's sparse vector is derived from it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def export_inputs(database_url: str, dataset_id: str, output: Path) -> int:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            rows = await connection.execute(
                text(
                    """
                    SELECT c.chunk_id,
                           concat_ws(E'\\n\\n', NULLIF(d.title, ''), NULLIF(c.section_title, ''), c.text)
                             AS lexical_text
                    FROM chunks c
                    JOIN documents d ON d.dataset_id = c.dataset_id AND d.id = c.document_id
                    WHERE c.dataset_id = :dataset_id
                      AND c.semantic_eligible IS TRUE
                      AND NOT d.is_external
                    ORDER BY c.chunk_id
                    """
                ),
                {"dataset_id": dataset_id},
            )
            values = [
                {"passage_id": str(row.chunk_id), "lexical_text": str(row.lexical_text or "").strip()}
                for row in rows
                if row.lexical_text and str(row.lexical_text).strip()
            ]
    finally:
        await engine.dispose()
    if not values:
        raise ValueError("no answer-ready semantic passages were found for this dataset")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values), encoding="utf-8")
    return len(values)


def main() -> int:
    load_dotenv(Path(__file__).resolve().parents[3] / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.getenv("RUNTIME_DATABASE_URL") or os.getenv("DATABASE_URL"))
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.database_url:
        raise ValueError("--database-url or RUNTIME_DATABASE_URL/DATABASE_URL is required")
    count = asyncio.run(export_inputs(args.database_url, args.dataset_id, args.output))
    print(json.dumps({"dataset_id": args.dataset_id, "passages": count, "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
