#!/usr/bin/env python3
"""Separate benchmark collection from production promotion.

Implementation blockers prevent starting a benchmark. Human/live evidence
blockers prevent promotion, but are expected before the benchmark exists.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _status_rows(plan: str) -> list[dict[str, str]]:
    """Parse either the current Vietnamese status ledger or legacy English one."""
    rows: list[dict[str, str]] = []
    in_ledger = False
    status_index = 2
    for line in plan.splitlines():
        if line.startswith("| Area | Current evidence | Status |"):
            in_ledger = True
            status_index = 2
            continue
        if line.startswith("| Hạng mục | Trạng thái | Điều kiện đóng |"):
            in_ledger = True
            status_index = 1
            continue
        if in_ledger and (line.startswith("Do not run") or not line.startswith("|")):
            if line.strip() and not line.lstrip().startswith("|"):
                in_ledger = False
            break
        if not in_ledger or not line.startswith("|"):
            continue
        columns = [value.strip() for value in line.strip("|").split("|")]
        if len(columns) != 3 or columns[0] == "---" or set(columns) == {"---"}:
            continue
        if status_index == 2:
            rows.append({"area": columns[0], "evidence": columns[1], "status": columns[2]})
        else:
            rows.append({"area": columns[0], "evidence": "", "status": columns[1]})
    return rows


def main() -> int:
    rows = _status_rows((ROOT / "PLAN.md").read_text(encoding="utf-8"))
    # The authoritative PLAN is Vietnamese; retain the legacy English tokens
    # for older branches but treat explicit negative status words as blockers.
    blocker_markers = (
        "partial", "not implemented", "not passed", "pending", "chưa", "đang fail",
        "lệch", "thiếu", "chưa đạt", "chưa được",
    )
    production_blockers = [
        row for row in rows
        if any(marker in row["status"].casefold() for marker in blocker_markers)
    ]
    implementation_blockers = [
        row for row in rows
        if any(marker in row["status"].casefold() for marker in ("not implemented", "implementation missing", "unwired"))
    ]
    report = {
        "promotion_benchmark_allowed": not implementation_blockers,
        "benchmark_collection_allowed": not implementation_blockers,
        "production_promotion_allowed": not production_blockers,
        "implementation_blockers": [{"area": row["area"], "status": row["status"]} for row in implementation_blockers],
        "production_blockers": [{"area": row["area"], "status": row["status"]} for row in production_blockers],
        "rule": "Benchmark collection requires implementation completeness; production promotion additionally requires all independent evidence gates.",
    }
    output = ROOT / "eval/results/promotion-gate-current.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "benchmark_collection_allowed": report["benchmark_collection_allowed"],
        "production_promotion_allowed": report["production_promotion_allowed"],
        "implementation_blocker_count": len(implementation_blockers),
        "production_blocker_count": len(production_blockers),
        "implementation_blockers": [row["area"] for row in implementation_blockers],
        "production_blockers": [row["area"] for row in production_blockers],
    }))
    return 0 if not implementation_blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
