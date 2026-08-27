#!/usr/bin/env python3
"""Refuse promotion benchmarks while PLAN execution rows remain incomplete."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _status_rows(plan: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    in_ledger = False
    for line in plan.splitlines():
        if line.startswith("| Area | Current evidence | Status |"):
            in_ledger = True
            continue
        if in_ledger and line.startswith("Do not run"):
            break
        if not in_ledger or not line.startswith("|"):
            continue
        columns = [value.strip() for value in line.strip("|").split("|")]
        if len(columns) != 3 or columns[0] == "---":
            continue
        rows.append({"area": columns[0], "evidence": columns[1], "status": columns[2]})
    return rows


def main() -> int:
    rows = _status_rows((ROOT / "PLAN.md").read_text(encoding="utf-8"))
    blockers = [
        row for row in rows
        if any(marker in row["status"].casefold() for marker in ("partial", "not implemented", "not passed", "pending"))
    ]
    report = {
        "promotion_benchmark_allowed": not blockers,
        "blockers": [{"area": row["area"], "status": row["status"]} for row in blockers],
        "rule": "No live model benchmark is a promotion decision while any PLAN ledger row is incomplete.",
    }
    output = ROOT / "eval/results/promotion-gate-current.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"promotion_benchmark_allowed": report["promotion_benchmark_allowed"], "blocker_count": len(blockers)}))
    return 0 if not blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
