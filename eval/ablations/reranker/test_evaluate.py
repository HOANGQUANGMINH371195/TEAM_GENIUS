import json

from eval.ablations.reranker.evaluate import run_ablation


def test_reranker_ablation_preserves_source_hash_and_reports_ir_metrics(tmp_path):
    path = tmp_path / "candidates.jsonl"
    rows = [
        {
            "case_id": "c1",
            "query": "mức hưởng điều kiện",
            "relevant_ids": ["p2"],
            "candidates": [
                {"chunk_id": "p1", "document_id": "d1", "content": "Thông tin chung", "score": 0.9},
                {"chunk_id": "p2", "document_id": "d2", "content": "Mức hưởng và điều kiện áp dụng", "score": 0.8},
            ],
        }
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    report = run_ablation(path)
    assert len(report["source_sha256"]) == 64
    assert report["variants"]["rrf_score_order"]["cases"] == 1
    assert "@1" in report["variants"]["heuristic_sentence_coverage"]["recall"]
