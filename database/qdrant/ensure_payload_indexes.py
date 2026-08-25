#!/usr/bin/env python3
"""Repair required payload indexes on an existing active Qdrant collection."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from qdrant_client import QdrantClient

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from database.corpus.qdrant_release import PAYLOAD_INDEXES  # noqa: E402


def main() -> int:
    load_dotenv()
    url, api_key = os.getenv("QDRANT_URL", ""), os.getenv("QDRANT_API_KEY", "")
    collection = os.getenv("QDRANT_COLLECTION", "medical_legal_active")
    if not url or not api_key:
        raise SystemExit("QDRANT_URL and QDRANT_API_KEY are required")
    client = QdrantClient(url=url, api_key=api_key, timeout=60)
    if not client.collection_exists(collection):
        raise SystemExit(f"Collection does not exist: {collection}")
    for field_name, field_type in PAYLOAD_INDEXES:
        client.create_payload_index(
            collection, field_name=field_name, field_schema=field_type, wait=True, timeout=60
        )
        print(f"indexed {collection}.{field_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
