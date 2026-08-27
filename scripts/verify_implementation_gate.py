#!/usr/bin/env python3
"""Verify that PLAN capabilities are implemented before any live benchmark."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    required = {
        "runtime": ROOT / "src/services/chat.py",
        "route": ROOT / "src/domain/route_plan.py",
        "reranker": ROOT / "src/services/reranker.py",
        "planner": ROOT / "src/services/planner.py",
        "experience": ROOT / "src/services/experience_retrieval.py",
        "memory_queue": ROOT / "src/services/research_jobs.py",
        "durable_worker": ROOT / "src/research_worker.py",
        "worker_blueprint": ROOT / "render-research-worker.yaml",
        "suite_compiler": ROOT / "eval/prepare_plan_suite.py",
        "auditor_ablation": ROOT / "eval/ablations/auditor/evaluate.py",
        "graph_ablation": ROOT / "eval/ablations/typed-graph/evaluate.py",
        "planning_ablation": ROOT / "eval/ablations/grounded-planning/evaluate.py",
        "reranker_ablation": ROOT / "eval/ablations/reranker/evaluate.py",
        "production_evidence_collector": ROOT / "eval/collect_production_evidence.py",
        "attestation": ROOT / "scripts/verify_production_attestation.py",
        "rollback": ROOT / "ops/runbooks/rollback.md",
        "outage": ROOT / "ops/runbooks/provider-outage.md",
    }
    checks = {name: path.is_file() for name, path in required.items()}
    config = (ROOT / "src/config.py").read_text(encoding="utf-8")
    checks["all_feature_flags"] = all(
        f"feature_{name}_enabled" in config
        for name in ("planner", "reranker", "auditor", "calculator", "viewer", "graph", "global_search", "experience_retrieval")
    )
    report = {"implementation_gate_pass": all(checks.values()), "checks": checks}
    path = ROOT / "eval/results/implementation-gate-current.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"implementation_gate_pass": report["implementation_gate_pass"], "missing": [name for name, ok in checks.items() if not ok]}))
    return 0 if report["implementation_gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
