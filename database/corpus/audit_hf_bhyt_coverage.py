#!/usr/bin/env python3
"""Audit a candidate corpus against current central-BHYT metadata from HF.

This is a coverage gate, not an ingestion mechanism.  It makes absence of an
officially listed central BHYT instrument visible before a corpus can be
released, while deliberately excluding local instruments from a national
assistant's default corpus.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from database.corpus.hydrate_vbpl_official import signature_variants

CURRENT_STATUSES = {"Còn hiệu lực", "Hết hiệu lực một phần"}


def is_current_central_bhyt(row: dict[str, Any]) -> bool:
    searchable = " ".join(str(row.get(field) or "") for field in ("title", "linh_vuc", "nganh")).casefold()
    return (
        row.get("pham_vi") == "Trung ương"
        and row.get("tinh_trang_hieu_luc") in CURRENT_STATUSES
        and "bảo hiểm y tế" in searchable
    )


def missing_signatures(source_rows: Iterable[dict[str, Any]], candidate_rows: Iterable[dict[str, str]]) -> list[dict[str, Any]]:
    candidate_variants = [signature_variants(row.get("so_ky_hieu", "")) for row in candidate_rows]
    missing = []
    for row in source_rows:
        if is_current_central_bhyt(row) and not any(signature_variants(str(row.get("so_ky_hieu") or "")) & value for value in candidate_variants):
            missing.append(row)
    return sorted(missing, key=lambda value: (str(value.get("so_ky_hieu") or ""), str(value.get("id") or "")))


def load_hf_metadata(path: Path) -> list[dict[str, Any]]:
    try:
        import pyarrow.parquet as pq
    except ImportError as error:  # pragma: no cover - local optional dependency
        raise RuntimeError("Install the one-off reader with: uv run --with pyarrow python ...") from error
    return pq.read_table(path).to_pylist()


def read_csv(path: Path) -> list[dict[str, str]]:
    csv.field_size_limit(sys.maxsize)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hf-metadata", type=Path, required=True)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source_rows = load_hf_metadata(args.hf_metadata)
    candidate_rows = read_csv(args.candidate_dir / "metadata.csv")
    expected = [row for row in source_rows if is_current_central_bhyt(row)]
    missing = missing_signatures(source_rows, candidate_rows)
    result = {
        "status": "pass" if not missing else "fail",
        "source": "th1nhng0/vietnamese-legal-documents",
        "scope": "current central BHYT instruments only",
        "expected_documents": len(expected),
        "candidate_documents": len(candidate_rows),
        "covered_documents": len(expected) - len(missing),
        "missing": [
            {key: row.get(key, "") for key in ("id", "so_ky_hieu", "title", "tinh_trang_hieu_luc")}
            for row in missing
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
