import importlib.util


def _load():
    spec = importlib.util.spec_from_file_location("typed_graph_ablation", "eval/ablations/typed-graph/evaluate.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.run_ablation


def test_typed_graph_ablation_reports_path_precision_and_outage_fallback(tmp_path):
    path = tmp_path / "graph.jsonl"
    path.write_text(
        '{"case_id":"c1","source_sha256":"a","gold_path_ids":["p1"],"document_graph_path_ids":["p2"],"typed_graph_path_ids":["p1"],"neo4j_outage":true,"fallback_valid":true}\n',
        encoding="utf-8",
    )
    report = _load()(path)
    assert report["variants"]["typed_fact_ppr"]["path_precision"] == 1.0
    assert report["outage_degradation"]["fallback_rate"] == 1.0
