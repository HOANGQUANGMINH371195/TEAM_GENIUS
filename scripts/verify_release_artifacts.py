#!/usr/bin/env python3
"""Verify that a mounted release benchmark matches the immutable suite lock.

The corpus artifacts intentionally live outside the source checkout.  This
command therefore has two explicit outcomes: ``available=false`` for a
source-only checkout (which is not a release-data verification), or a
fail-closed hash/coverage verification when the files are mounted.  It never
generates a replacement benchmark and never treats absence as success.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def verify(root: Path) -> dict[str, Any]:
    suite = root / "eval/cases/snapshot-c439751724ab7f10.jsonl"
    release = root / "data/clean/medical_active_v31_fully_reviewed/release_benchmark.jsonl"
    semantic = root / "data/clean/medical_active_v31_fully_reviewed/semantic_question_benchmark.jsonl"
    missing = [str(path.relative_to(root)) for path in (release, semantic) if not path.is_file()]
    if missing:
        return {
            "available": False,
            "verified": False,
            "missing": missing,
            "reason": "release artifacts are external and were not mounted",
        }
    if not suite.is_file():
        return {"available": True, "verified": False, "errors": ["locked_suite_missing"]}

    rows = _jsonl(suite)
    if not rows or not isinstance(rows[0].get("manifest"), dict):
        return {"available": True, "verified": False, "errors": ["locked_suite_manifest_missing"]}
    manifest = rows[0]["manifest"]
    cases = rows[1:]
    expected_hashes = {
        "release_benchmark_sha256": _sha256(release),
        "semantic_benchmark_sha256": _sha256(semantic),
    }
    errors = [
        key
        for key, value in expected_hashes.items()
        if manifest.get(key) != value
    ]
    if manifest.get("cases") != len(cases):
        errors.append("suite_case_count_mismatch")
    if len({str(case.get("case_id")) for case in cases}) != len(cases):
        errors.append("suite_case_ids_not_unique")
    return {
        "available": True,
        "verified": not errors,
        "release_sha256": expected_hashes["release_benchmark_sha256"],
        "semantic_sha256": expected_hashes["semantic_benchmark_sha256"],
        "suite_cases": len(cases),
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--require", action="store_true", help="fail if artifacts are not mounted")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = verify(args.root.resolve())
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["verified"] or (not args.require and not report["available"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
