#!/usr/bin/env python3
"""Assemble the release evaluation denominator without creating legal gold.

Each input must be a reviewed JSONL artifact.  This command only validates
counts, release identity, provenance and unique IDs before composing a
manifest; it never duplicates or paraphrases cases to meet a quota.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

QUOTAS = {
    "core": 300,
    "adversarial": 100,
    "table": 100,
    "temporal": 75,
    "multi_turn": 75,
    "unanswerable": 50,
}


def _read(path: Path, *, release_id: str) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if rows and isinstance(rows[0], dict) and "manifest" in rows[0]:
        rows = rows[1:]
    if not rows:
        raise ValueError(f"{path}: no cases")
    for row in rows:
        if not isinstance(row, dict) or not str(row.get("case_id") or "").strip():
            raise ValueError(f"{path}: every case needs case_id")
        case_release = str(row.get("dataset_id") or row.get("release_id") or release_id)
        if case_release != release_id:
            raise ValueError(f"{path}:{row['case_id']}: release mismatch")
        # A source hash or a reviewer reference is mandatory for every case;
        # accepting an unlabeled machine row would make the quota meaningless.
        if not str(row.get("source_sha256") or row.get("expected_evidence_sha256") or row.get("review_ref") or "").strip():
            raise ValueError(f"{path}:{row['case_id']}: provenance/review reference required")
        if str(row.get("review_status") or "").strip().casefold() != "accepted":
            raise ValueError(f"{path}:{row['case_id']}: review_status=accepted is required")
        review_labels = row.get("review_labels")
        if not isinstance(review_labels, list):
            raise ValueError(f"{path}:{row['case_id']}: review_labels from independent reviewers are required")
        reviewers = {
            str(label.get("reviewer") or "").strip()
            for label in review_labels
            if isinstance(label, dict)
        }
        if "" in reviewers or len(reviewers) < 2:
            raise ValueError(f"{path}:{row['case_id']}: at least two independent reviewers are required")
    return rows


def prepare(inputs: dict[str, Path], *, release_id: str, output: Path) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    all_ids: set[str] = set()
    for group, path in inputs.items():
        rows = _read(path, release_id=release_id)
        if len(rows) < QUOTAS[group]:
            raise ValueError(f"{group}: {len(rows)} cases, need at least {QUOTAS[group]}")
        if all_ids & {str(row["case_id"]) for row in rows}:
            raise ValueError(f"duplicate case IDs across groups: {group}")
        all_ids.update(str(row["case_id"]) for row in rows)
        groups[group] = rows
    manifest = {
        "artifact": "plan-evaluation-suite-v1",
        "dataset_id": release_id,
        "cases": sum(len(rows) for rows in groups.values()),
        "groups": {group: len(rows) for group, rows in groups.items()},
        "source_sha256": {group: hashlib.sha256(path.read_bytes()).hexdigest() for group, path in inputs.items()},
        "gold_policy": "reviewed_external_labels_only",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({"manifest": manifest}, ensure_ascii=False, sort_keys=True) + "\n")
        for group, rows in groups.items():
            for row in rows:
                handle.write(json.dumps({**row, "suite_group": group}, ensure_ascii=False, sort_keys=True) + "\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--core", type=Path, required=True)
    parser.add_argument("--adversarial", type=Path, required=True)
    parser.add_argument("--table", type=Path, required=True)
    parser.add_argument("--temporal", type=Path, required=True)
    parser.add_argument("--multi-turn", type=Path, required=True)
    parser.add_argument("--unanswerable", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inputs = {
        "core": args.core, "adversarial": args.adversarial, "table": args.table,
        "temporal": args.temporal, "multi_turn": args.multi_turn, "unanswerable": args.unanswerable,
    }
    manifest = prepare(inputs, release_id=args.release_id, output=args.output)
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
