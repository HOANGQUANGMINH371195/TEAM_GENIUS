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
        "viewer_acceptance_tests": ROOT / "tests/test_api/test_document_viewer_endpoint.py",
        "typed_ontology": ROOT / "docs/data/typed-bhyt-ontology.md",
        "release_lock": ROOT / "docs/data/release-lock-snapshot-c439751724ab7f10.json",
        "cache_runbook": ROOT / "ops/runbooks/cache.md",
        "release_runbook": ROOT / "ops/runbooks/release.md",
        "rollback_runbook": ROOT / "ops/runbooks/rollback.md",
        "outage_runbook": ROOT / "ops/runbooks/provider-outage.md",
        "retention_runbook": ROOT / "ops/runbooks/supabase-retention.md",
        "research_worker_runbook": ROOT / "ops/runbooks/research-worker.md",
        "research_worker_blueprint": ROOT / "render-research-worker.yaml",
        "plan_suite_compiler": ROOT / "eval/prepare_plan_suite.py",
        "implementation_gate": ROOT / "scripts/verify_implementation_gate.py",
        "route_module": ROOT / "src/domain/route_plan.py",
        "calculator_module": ROOT / "src/services/calculator.py",
        "viewer_module": ROOT / "src/services/document_viewer.py",
        "cache_module": ROOT / "src/services/conversation_cache.py",
        "typed_fact_module": ROOT / "src/domain/facts.py",
        "typed_fact_migration": ROOT / "database/postgres/migrations/20260827_typed_legal_facts.sql",
        "golden_calculator": ROOT / "eval/cases/calculator-golden-v1.jsonl",
        "batch_manifest": ROOT / "eval/batch_manifest.py",
        "batch_provider_adapter": ROOT / "eval/openai_batch.py",
        "calibration_metrics": ROOT / "eval/calibration.py",
        "typed_fact_importer": ROOT / "database/neo4j/scripts/import_typed_facts.py",
        "typed_graph_acceptance_tests": ROOT / "tests/test_integrations/test_neo4j_typed_facts.py",
        "neo4j_cleanup_guard": ROOT / "database/neo4j/scripts/cleanup_stale_release.py",
        "scenario_page": ROOT / "web/app/calculator/page.tsx",
        "promotion_gate": ROOT / "scripts/verify_promotion_gate.py",
        "reranker_backend": ROOT / "src/services/reranker.py",
        "reranker_ablation_harness": ROOT / "eval/ablations/reranker/evaluate.py",
        "community_retrieval": ROOT / "src/services/global_retrieval.py",
        "community_index_builder": ROOT / "database/corpus/build_community_index.py",
        "research_job_worker": ROOT / "src/services/research_jobs.py",
        "research_worker_entrypoint": ROOT / "src/research_worker.py",
        "production_attestation_verifier": ROOT / "scripts/verify_production_attestation.py",
        "production_attestation_template": ROOT / "ops/attestations/production-attestation.template.json",
        "parity_verifier": ROOT / "database/corpus/verify_live_corpus_parity.py",
        "experience_retrieval": ROOT / "src/services/experience_retrieval.py",
        "auditor_ablation": ROOT / "eval/ablations/auditor/evaluate.py",
        "typed_graph_ablation": ROOT / "eval/ablations/typed-graph/evaluate.py",
        "grounded_planning_ablation": ROOT / "eval/ablations/grounded-planning/evaluate.py",
    }
    checks = {name: path.is_file() for name, path in required.items()}
    fixture = _fixture_case_count(required["golden_calculator"])
    checks["golden_calculator_manifest_matches_rows"] = fixture is not None and fixture[0] == fixture[1] == 100

    config_text = (ROOT / "src/config.py").read_text(encoding="utf-8")
    calibration_text = (ROOT / "eval/calibration.py").read_text(encoding="utf-8")
    checks["independent_calibration_panel_validator"] = "validate_calibration_panel" in calibration_text
    checks["feature_flags_declared"] = all(
        f"feature_{name}_enabled" in config_text
        for name in ("planner", "reranker", "auditor", "calculator", "viewer", "graph", "global_search", "experience_retrieval")
    )
    route_text = (ROOT / "src/agents/nodes/graphrag_nodes.py").read_text(encoding="utf-8")
    checks["route_plan_recorded_in_intake"] = "route_plan" in route_text
    checks["grounded_plan_recorded_after_retrieval"] = "grounded_plan" in route_text
    checks["release_scoped_context_cache"] = "release_id" in (ROOT / "src/services/conversation_cache.py").read_text(encoding="utf-8")
    chat_text = (ROOT / "src/services/chat.py").read_text(encoding="utf-8")
    neo4j_text = (ROOT / "src/integrations/neo4j.py").read_text(encoding="utf-8")
    checks["route_deadline_fallback"] = "route:budget_fallback" in chat_text
    checks["generation_timeout_contract"] = "timeout_seconds" in chat_text
    checks["typed_fact_bounded_walk"] = "bounded_typed_ppr" in neo4j_text

    report = {
        "repository_contract_pass": all(checks.values()),
        "checks": checks,
        "external_gates_still_required": [
            "independent human-adjudicated accuracy and no-catastrophic-error review",
            "authenticated cold/warm/concurrency latency and SSE TTFT run",
            "managed Render/Vercel smoke, rollback, and provider outage drill",
            "ablation evidence for learned reranker, typed graph, and grounded planning",
            "durable async worker deployment and release-scoped community index artifact",
        ],
    }
    output = ROOT / "eval/results/plan-contract-current.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"repository_contract_pass": report["repository_contract_pass"], "output": str(output)}))
    return 0 if report["repository_contract_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
