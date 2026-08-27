from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatRequest(ApiModel):
    message: str = Field(
        ...,
        min_length=1,
        max_length=5000,
        description="Câu hỏi của người dùng về BHYT hoặc viện phí.",
        examples=["Quyền lợi BHYT khi khám trái tuyến là gì?"],
    )
    conversation_id: str = Field(default="", max_length=128)
    turn_id: str = Field(default="", max_length=128)

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("message must not be blank")
        return normalized

class AnalyzeRequest(ApiModel):
    message: str = Field(
        ...,
        min_length=1,
        max_length=5000,
        description="Nội dung cần phân tích.",
        examples=["Hóa đơn có khoản thu nào cần kiểm tra?"],
    )

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("message must not be blank")
        return normalized


class ChatCitation(ApiModel):
    title: str = Field(default="", description="Tên văn bản nguồn.")
    document_number: str = Field(default="", description="Số hiệu văn bản công khai nếu có.")
    section_title: str = Field(default="", description="Tiêu đề điều/mục nếu có.")
    quote: str = Field(default="", description="Đoạn trích công khai dùng để trả lời.")
    source_url: str = Field(default="", description="URL nguồn chính thức nếu là metadata.")
    source_checked_at: str = Field(default="", description="Thời điểm kiểm tra provenance.")


class ChatResponse(ApiModel):
    response: str = Field(..., description="Câu trả lời dựa trên nguồn pháp lý đã kiểm tra.")
    citations: list[ChatCitation] = Field(
        default_factory=list,
        description="Các nguồn pháp lý công khai hỗ trợ câu trả lời.",
    )


class AnalyzeResponse(ApiModel):
    analysis: str = Field(..., description="Kết quả phân tích input.")


class BenefitCalculationRequest(ApiModel):
    """Calculator input; rule values must come from verified table facts."""

    covered_cost: str = Field(..., min_length=1, max_length=40)
    base_rate_percent: str = Field(..., min_length=1, max_length=20)
    copayment_spend: str = Field(default="0", max_length=40)
    copayment_threshold: str | None = Field(default=None, max_length=40)
    continuous_years: str | None = Field(default=None, max_length=20)
    required_years: str = Field(default="5", max_length=20)
    threshold_rate_percent: str = Field(default="100", max_length=20)
    rule_provenance: list[str] = Field(default_factory=list, max_length=8)


class BenefitCalculationResponse(ApiModel):
    covered_cost: str
    applied_rate_percent: str
    insurer_pays: str
    patient_pays: str
    threshold_met: bool
    formula_id: str
    provenance: list[str]


class ConversationSummary(ApiModel):
    conversation_id: str
    title: str = ""
    updated_at: datetime


class ConversationTurn(ApiModel):
    turn_id: str
    user_message: str
    assistant_response: str
    citations: list[ChatCitation] = Field(default_factory=list)
    created_at: datetime


class ReviewQueueItem(ApiModel):
    review_id: str
    domain: Literal["legal_document", "hospital_fee_ocr"]
    source_id: str = ""
    title: str = ""
    status: Literal["pending", "accepted", "rejected"]
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    submitted_by: str = ""
    assigned_to: str = ""
    decision_note: str = ""
    created_at: datetime
    updated_at: datetime
    decided_at: datetime | None = None
    audit: list[dict[str, Any]] = Field(default_factory=list)


class ReviewDecisionRequest(ApiModel):
    status: Literal["accepted", "rejected"]
    note: str = Field(default="", max_length=2000)


class AgentStatusResponse(ApiModel):
    status: str = Field(..., description="Trạng thái agent.")
    agent: str = Field(..., description="Tên và phiên bản agent.")


class ErrorResponse(ApiModel):
    code: str = Field(..., description="Mã lỗi ổn định cho client.")
    message: str = Field(..., description="Thông báo lỗi an toàn cho người dùng.")
    request_id: str = Field(..., description="ID dùng để tra log server-side.")


class ReadinessResponse(ApiModel):
    status: str
    database: bool
    qdrant: bool
    neo4j: bool
    llm: bool
    embedding: bool
    details: dict[str, Any] = Field(default_factory=dict)
