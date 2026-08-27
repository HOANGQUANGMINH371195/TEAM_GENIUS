#!/usr/bin/env python3
"""Compare document-graph and typed-fact paths on reviewed IR traces.

Rows contain retrieved path IDs and an independently reviewed set of valid
paths.  The script reports path precision/recall and outage fallback; it does
not infer correctness from graph connectivity or node counts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def _load(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if rows and "manifest" in rows[0]:
        rows = rows[1:]
    if not rows:
        raise ValueError("typed graph artifact has no cases")
    required = {"case_id", "gold_path_ids", "document_graph_path_ids", "typed_graph_path_ids", "source_sha256"}
    for row in rows:
        missing = required - set(row)
        if missing:
            raise ValueError(f"{row.get('case_id', '<unknown>')}: missing {sorted(missing)}")
        if not row["source_sha256"]:
            raise ValueError(f"{row['case_id']}: source hash required")
    return rows


def _score(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    precisions: list[float] = []
    recalls: list[float] = []
    for row in rows:
        gold = {str(value) for value in row["gold_path_ids"]}
        got = {str(value) for value in row[field]}
        if not gold:
            continue
        precisions.append(len(gold & got) / len(got) if got else 0.0)
        recalls.append(len(gold & got) / len(gold))
    return {
        "cases": len(rows),
        "eligible": len(recalls),
        "path_precision": sum(precisions) / len(precisions) if precisions else None,
        "path_recall": sum(recalls) / len(recalls) if recalls else None,
    }


def run_ablation(path: Path) -> dict[str, Any]:
    rows = _load(path)
    outage_rows = [row for row in rows if bool(row.get("neo4j_outage"))]
    fallback_ok = sum(bool(row.get("fallback_valid")) for row in outage_rows)
    return {
        "artifact": "typed-graph-ablation-v1",
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "variants": {
            "document_graph": _score(rows, "document_graph_path_ids"),
            "typed_fact_ppr": _score(rows, "typed_graph_path_ids"),
        },
        "outage_degradation": {
            "cases": len(outage_rows),
            "fallback_valid": fallback_ok,
            "fallback_rate": fallback_ok / len(outage_rows) if outage_rows else None,
        },
        "promotion_ready": False,
        "warning": "Graph paths are navigation only until canonical source hydration and independent review pass.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_ablation(args.artifact)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"artifact": report["artifact"], "cases": report["variants"]["typed_fact_ppr"]["cases"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
