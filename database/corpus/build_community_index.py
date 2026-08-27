#!/usr/bin/env python3
"""Build a deterministic, release-scoped community-summary index.

Input JSONL is a reviewed/curated annotation file.  This command does not run
an LLM or discover legal communities implicitly: each row must already carry a
``community_id`` and canonical ``passage_id``.  The output's summaries are
navigation hints only; serving must hydrate their document IDs from
PostgreSQL before using any text as evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from src.services.global_retrieval import build_community_summaries


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON at line {line_number}: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"row at line {line_number} must be an object")
        rows.append(value)
    if not rows:
        raise ValueError("community input has no rows")
    return rows


def build_index(input_path: Path, *, release_id: str) -> list[dict[str, object]]:
    summaries = build_community_summaries(_read_jsonl(input_path), release_id=release_id)
    if not summaries:
        raise ValueError("community input produced no summaries")
    return [summary.as_record() for summary in summaries]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="reviewed community passage annotations")
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.release_id.startswith("snapshot-"):
        parser.error("release-id must be an immutable snapshot-... identifier")
    summaries = build_index(args.input, release_id=args.release_id)
    payload = {
        "index": "community-summary-v1",
        "release_id": args.release_id,
        "source_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "communities": len(summaries),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        for summary in summaries:
            handle.write(json.dumps(summary, ensure_ascii=False, sort_keys=True) + "\n")
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
