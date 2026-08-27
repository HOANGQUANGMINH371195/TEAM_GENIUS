import importlib.util


def _load():
    spec = importlib.util.spec_from_file_location("planning_ablation", "eval/ablations/grounded-planning/evaluate.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.run_ablation


def test_planning_ablation_reports_unique_gain_and_budget(tmp_path):
    path = tmp_path / "planning.jsonl"
    path.write_text(
        '{"case_id":"c1","source_sha256":"a","gold_evidence_ids":["p1","p2"],"direct_evidence_ids":["p1"],"planned_evidence_ids":["p1","p2"],"direct_latency_ms":100,"planned_latency_ms":150,"direct_cost_units":1,"planned_cost_units":2,"duplicate_branches_cancelled":1}\n',
        encoding="utf-8",
    )
    report = _load()(path)
    assert report["variants"]["planned"]["coverage"] == 1.0
    assert report["unique_evidence_gain_cases"] == 1
    assert report["duplicate_branch_cancellations"] == 1
