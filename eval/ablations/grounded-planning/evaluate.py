#!/usr/bin/env python3
"""Compare direct retrieval with evidence-gap planning on reviewed traces."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from statistics import quantiles
from typing import Any


def _load(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if rows and "manifest" in rows[0]:
        rows = rows[1:]
    if not rows:
        raise ValueError("planning artifact has no cases")
    for row in rows:
        required = {"case_id", "gold_evidence_ids", "direct_evidence_ids", "planned_evidence_ids", "source_sha256"}
        missing = required - set(row)
        if missing:
            raise ValueError(f"{row.get('case_id', '<unknown>')}: missing {sorted(missing)}")
        if not row["source_sha256"]:
            raise ValueError(f"{row['case_id']}: source hash required")
        for field in ("direct_latency_ms", "planned_latency_ms", "direct_cost_units", "planned_cost_units"):
            if float(row.get(field, 0)) < 0:
                raise ValueError(f"{row['case_id']}: {field} must be non-negative")
    return rows


def _coverage(rows: list[dict[str, Any]], field: str) -> float | None:
    values = []
    for row in rows:
        gold = {str(item) for item in row["gold_evidence_ids"]}
        if gold:
            values.append(len(gold & {str(item) for item in row[field]}) / len(gold))
    return sum(values) / len(values) if values else None


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    return round(values[0] if len(values) == 1 else quantiles(values, n=100, method="inclusive")[94], 2)


def run_ablation(path: Path) -> dict[str, Any]:
    rows = _load(path)
    direct = _coverage(rows, "direct_evidence_ids")
    planned = _coverage(rows, "planned_evidence_ids")
    direct_latency = [float(row.get("direct_latency_ms", 0)) for row in rows]
    planned_latency = [float(row.get("planned_latency_ms", 0)) for row in rows]
    direct_cost = sum(float(row.get("direct_cost_units", 0)) for row in rows)
    planned_cost = sum(float(row.get("planned_cost_units", 0)) for row in rows)
    unique_gain = sum(
        bool(set(map(str, row["planned_evidence_ids"])) - set(map(str, row["direct_evidence_ids"])))
        for row in rows
    )
    return {
        "artifact": "grounded-planning-ablation-v1",
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "variants": {
            "direct": {"cases": len(rows), "coverage": direct, "p95_latency_ms": _p95(direct_latency), "cost_units": direct_cost},
            "planned": {"cases": len(rows), "coverage": planned, "p95_latency_ms": _p95(planned_latency), "cost_units": planned_cost},
        },
        "unique_evidence_gain_cases": unique_gain,
        "duplicate_branch_cancellations": sum(int(row.get("duplicate_branches_cancelled", 0)) for row in rows),
        "promotion_ready": False,
        "warning": "Planning is promotable only when independently reviewed coverage gain justifies latency and cost.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_ablation(args.artifact)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"artifact": report["artifact"], "cases": len(_load(args.artifact))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
