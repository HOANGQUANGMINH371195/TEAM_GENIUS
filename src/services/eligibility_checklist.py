"""Deterministic user-fact checklist for legally material BHYT questions.

The checklist never decides eligibility or stores a legal rate. It only asks
for user circumstances that can change which current rule must be retrieved.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ChecklistTopic = Literal[
    "benefit",
    "five_year",
    "referral",
    "emergency",
    "student_contribution",
]


@dataclass(frozen=True)
class ChecklistField:
    key: str
    label: str
    reason: str
    input_type: Literal["text", "date", "number", "boolean", "select"]
    options: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "reason": self.reason,
            "input_type": self.input_type,
            "options": list(self.options),
        }


FIELD_CATALOG = {
    "treatment_date": ChecklistField(
        "treatment_date", "Ngày khám hoặc điều trị", "Dùng để chọn văn bản có hiệu lực đúng thời điểm.", "date"
    ),
    "beneficiary_group": ChecklistField(
        "beneficiary_group", "Nhóm đối tượng tham gia BHYT", "Nhóm đối tượng có thể làm thay đổi mức và phạm vi hưởng.", "text"
    ),
    "care_type": ChecklistField(
        "care_type", "Hình thức điều trị", "Nội trú và ngoại trú có thể áp dụng điều kiện khác nhau.", "select", ("outpatient", "inpatient")
    ),
    "facility_level": ChecklistField(
        "facility_level", "Cấp chuyên môn của cơ sở khám chữa bệnh", "Cấp cơ sở có thể làm thay đổi quy tắc chuyển cơ sở và mức thanh toán.", "text"
    ),
    "emergency": ChecklistField(
        "emergency", "Có phải trường hợp cấp cứu không?", "Cấp cứu là tình tiết có thể thay đổi yêu cầu chuyển cơ sở.", "boolean"
    ),
    "referral_status": ChecklistField(
        "referral_status", "Có giấy chuyển cơ sở hợp lệ không?", "Tình trạng chuyển cơ sở có thể làm thay đổi mức hưởng.", "boolean"
    ),
    "referral_document_date": ChecklistField(
        "referral_document_date", "Ngày lập giấy chuyển cơ sở", "Dùng để kiểm tra thời hạn giấy theo quy định tại thời điểm điều trị.", "date"
    ),
    "continuous_participation_start": ChecklistField(
        "continuous_participation_start", "Ngày bắt đầu tham gia BHYT liên tục", "Dùng để xác định khoảng tham gia, không tự suy đoán đủ năm.", "date"
    ),
    "copayment_paid": ChecklistField(
        "copayment_paid", "Số tiền cùng chi trả đã thanh toán", "Cần cho phép so sánh với ngưỡng pháp lý được truy xuất mới.", "number"
    ),
    "education_level": ChecklistField(
        "education_level", "Bậc/cơ sở giáo dục", "Nhóm học sinh, sinh viên có thể thuộc cơ chế đóng và hỗ trợ khác nhau.", "text"
    ),
    "school_year": ChecklistField(
        "school_year", "Năm học áp dụng", "Dùng để chọn mức tham chiếu và chính sách hỗ trợ đúng thời điểm.", "text"
    ),
}

TOPIC_FIELDS: dict[ChecklistTopic, tuple[str, ...]] = {
    "benefit": (
        "treatment_date", "beneficiary_group", "care_type", "facility_level", "emergency",
    ),
    "five_year": (
        "treatment_date", "beneficiary_group", "continuous_participation_start", "copayment_paid",
    ),
    "referral": (
        "treatment_date", "care_type", "facility_level", "emergency",
    ),
    "emergency": (
        "treatment_date", "care_type", "facility_level", "emergency",
    ),
    "student_contribution": (
        "beneficiary_group", "education_level", "school_year",
    ),
}


class ChecklistInputError(ValueError):
    """Raised when a checklist request contains unknown or malformed facts."""


def build_eligibility_checklist(
    topic: ChecklistTopic,
    facts: dict[str, Any],
) -> dict[str, object]:
    """Return only currently missing, outcome-material user facts."""
    if topic not in TOPIC_FIELDS:
        raise ChecklistInputError("unsupported checklist topic")
    unknown = sorted(set(facts) - set(FIELD_CATALOG))
    if unknown:
        raise ChecklistInputError("unknown fact fields: " + ", ".join(unknown))

    required = list(TOPIC_FIELDS[topic])
    emergency = facts.get("emergency")
    if emergency is False and topic in {"benefit", "referral", "emergency"}:
        required.append("referral_status")
    if facts.get("referral_status") is True:
        required.append("referral_document_date")

    supplied = {
        key for key, value in facts.items()
        if value is not None and (not isinstance(value, str) or value.strip())
    }
    missing = [FIELD_CATALOG[key] for key in dict.fromkeys(required) if key not in supplied]
    return {
        "topic": topic,
        "complete": not missing,
        "missing": [field.as_dict() for field in missing],
        "accepted_fact_keys": sorted(supplied),
        "next_question": missing[0].label if missing else "",
        "legal_retrieval_required": True,
    }
