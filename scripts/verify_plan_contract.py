#!/usr/bin/env python3
"""Check that the forward-plan delivery contracts are present.

This is a repository gate only.  It deliberately does not mark human accuracy,
managed-provider latency, or production availability as passing; those require
an authenticated independent run.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _fixture_case_count(path: Path) -> tuple[int, int] | None:
    if not path.is_file():
        return None
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows or not isinstance(rows[0], dict):
        return None
    manifest = rows[0].get("manifest") or {}
    return int(manifest.get("cases", -1)), len(rows) - 1


def main() -> int:
    required = {
        "route_contract": ROOT / "docs/architecture/route-contract.md",
        "batch_contract": ROOT / "docs/architecture/batch-contract.md",
        "calculator_contract": ROOT / "docs/product/calculator-contract.md",
        "viewer_security": ROOT / "docs/product/document-viewer-security.md",
        "typed_ontology": ROOT / "docs/data/typed-bhyt-ontology.md",
        "cache_runbook": ROOT / "ops/runbooks/cache.md",
        "outage_runbook": ROOT / "ops/runbooks/provider-outage.md",
        "retention_runbook": ROOT / "ops/runbooks/supabase-retention.md",
        "route_module": ROOT / "src/domain/route_plan.py",
        "calculator_module": ROOT / "src/services/calculator.py",
        "viewer_module": ROOT / "src/services/document_viewer.py",
        "cache_module": ROOT / "src/services/conversation_cache.py",
        "typed_fact_module": ROOT / "src/domain/facts.py",
        "typed_fact_migration": ROOT / "database/postgres/migrations/20260827_typed_legal_facts.sql",
        "golden_calculator": ROOT / "eval/cases/calculator-golden-v1.jsonl",
        "batch_manifest": ROOT / "eval/batch_manifest.py",
        "calibration_metrics": ROOT / "eval/calibration.py",
        "typed_fact_importer": ROOT / "database/neo4j/scripts/import_typed_facts.py",
        "scenario_page": ROOT / "web/app/calculator/page.tsx",
    }
    checks = {name: path.is_file() for name, path in required.items()}
    fixture = _fixture_case_count(required["golden_calculator"])
    checks["golden_calculator_manifest_matches_rows"] = fixture is not None and fixture[0] == fixture[1] == 100

    config_text = (ROOT / "src/config.py").read_text(encoding="utf-8")
    checks["feature_flags_declared"] = all(
        f"feature_{name}_enabled" in config_text
        for name in ("planner", "reranker", "auditor", "calculator", "viewer", "graph")
    )
    route_text = (ROOT / "src/agents/nodes/graphrag_nodes.py").read_text(encoding="utf-8")
    checks["route_plan_recorded_in_intake"] = "route_plan" in route_text
    checks["grounded_plan_recorded_after_retrieval"] = "grounded_plan" in route_text
    checks["release_scoped_context_cache"] = "release_id" in (ROOT / "src/services/conversation_cache.py").read_text(encoding="utf-8")

    report = {
        "repository_contract_pass": all(checks.values()),
        "checks": checks,
        "external_gates_still_required": [
            "independent human-adjudicated accuracy and no-catastrophic-error review",
            "authenticated cold/warm/concurrency latency and SSE TTFT run",
            "managed Render/Vercel smoke, rollback, and provider outage drill",
            "ablation evidence for learned reranker, typed graph, and grounded planning",
        ],
    }
    output = ROOT / "eval/results/plan-contract-current.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"repository_contract_pass": report["repository_contract_pass"], "output": str(output)}))
    return 0 if report["repository_contract_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
