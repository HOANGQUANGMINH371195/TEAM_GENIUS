from __future__ import annotations

from eval.compare_rag_baseline import evaluate, rank


def test_lexical_rank_prefers_exact_public_identifier() -> None:
    documents = [
        {"document_id": "wrong", "title": "Một nghị quyết khác", "so_ky_hieu": "12/2024/NQ", "text": "khác", "_tokens": {"khác"}},
        {"document_id": "target", "title": "Nghị quyết hỗ trợ BHYT", "so_ky_hieu": "60/2026/NQ-HĐND", "text": "BHYT", "_tokens": {"bhyt"}},
    ]
    ranked = rank("Văn bản 60/2026/NQ-HĐND có tên gì?", documents, 1)
    assert ranked[0]["document_id"] == "target"


def test_baseline_without_answers_has_no_fake_quality_score() -> None:
    cases = {
        "case-1": {
            "reference_context_ids": ["target"],
            "required_facts": [{"name": "title", "value": "Nghị quyết hỗ trợ BHYT"}],
        }
    }
    result = evaluate(
        "ordinary_lexical_rag",
        cases,
        [{"case_id": "case-1", "answer": "", "retrieved_contexts": [{"document_id": "target"}]}],
        (1, 5),
    )
    assert result["retrieval"]["hit@1"] == 1.0
    assert result["answer_surface_fact_coverage"] is None
    assert result["answer_surface_denominator"] == 0
