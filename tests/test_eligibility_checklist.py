import pytest

from src.services.eligibility_checklist import (
    ChecklistInputError,
    build_eligibility_checklist,
)


def test_checklist_asks_only_currently_missing_material_facts():
    result = build_eligibility_checklist(
        "referral",
        {
            "treatment_date": "2026-08-28",
            "care_type": "inpatient",
            "facility_level": "cấp chuyên sâu",
            "emergency": False,
        },
    )

    assert result["complete"] is False
    assert [item["key"] for item in result["missing"]] == ["referral_status"]
    assert result["legal_retrieval_required"] is True


def test_checklist_adds_referral_date_only_after_user_confirms_document():
    result = build_eligibility_checklist(
        "referral",
        {
            "treatment_date": "2026-08-28",
            "care_type": "inpatient",
            "facility_level": "cấp chuyên sâu",
            "emergency": False,
            "referral_status": True,
        },
    )
    assert [item["key"] for item in result["missing"]] == ["referral_document_date"]


def test_checklist_never_accepts_unbounded_fact_names():
    with pytest.raises(ChecklistInputError, match="unknown fact fields"):
        build_eligibility_checklist("benefit", {"legal_answer": "100%"})
