from __future__ import annotations

from eval.critical_bhyt_eval import _deterministic_findings, _read_fixture


def test_critical_fixture_is_well_formed() -> None:
    from pathlib import Path

    manifest, cases = _read_fixture(Path("eval/cases/critical-bhyt-7.jsonl"))
    assert manifest["cases"] == 7
    assert len(cases) == 7


def test_critical_eval_rejects_internal_id_and_missing_authority() -> None:
    case = {
        "expected_status": "answerable",
        "accepted_document_numbers": ["51/2024/QH15"],
        "required_facts": ["100% mức hưởng"],
    }
    output = {
        "response": "DOCUMENT_ID=opaque-doc: 100% mức hưởng.",
        "citations": [{"document_id": "opaque-doc", "chunk_id": "opaque-chunk", "document_number": "01/2020/TT-BYT"}],
        "retrieved_evidence": [],
    }

    findings = _deterministic_findings(case, output)

    assert findings["deterministic_status"] == "FAIL"
    assert "internal_id_leak" in findings["failures"]
    assert "accepted_authority_not_retrieved" in findings["failures"]


def test_critical_eval_only_marks_mechanical_checks_not_legal_truth() -> None:
    case = {
        "expected_status": "answerable",
        "accepted_document_numbers": ["51/2024/QH15"],
        "required_facts": ["100% mức hưởng"],
    }
    output = {
        "response": "Kết luận: 100% mức hưởng.",
        "citations": [{"document_number": "51/2024/QH15"}],
        "retrieved_evidence": [],
    }

    findings = _deterministic_findings(case, output)

    assert findings["deterministic_status"] == "PASS"
    assert findings["failures"] == []
