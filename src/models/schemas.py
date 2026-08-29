from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GroundedAnswer(ApiModel):
    """Strict model-facing answer contract.

    Provenance is deliberately owned by the retrieval/guardrail pipeline;
    the model can only supply bounded prose fields.  This prevents fabricated
    document identifiers or citations from becoming part of the public API.
    """

    conclusion: str = Field(..., min_length=1, max_length=4000)
    conditions: list[str] = Field(default_factory=list, max_length=8)
    exceptions: list[str] = Field(default_factory=list, max_length=8)
    uncertainty: str | None = Field(default=None, max_length=1000)

    @field_validator("conditions", "exceptions")
    @classmethod
    def normalize_items(cls, values: list[str]) -> list[str]:
        cleaned = [" ".join(value.split()) for value in values if value and value.strip()]
        return cleaned[:8]

    @field_validator("uncertainty")
    @classmethod
    def normalize_uncertainty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None


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
    request_id: str = Field(default="", max_length=128)
    conversation_id: str = Field(default="", max_length=128)
    turn_id: str = Field(default="", max_length=128)


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


class BenefitCalculationScenario(ApiModel):
    label: str = Field(..., min_length=1, max_length=120)
    calculation: BenefitCalculationRequest


class BenefitCalculationScenariosRequest(ApiModel):
    scenarios: list[BenefitCalculationScenario] = Field(..., min_length=1, max_length=8)


class BenefitCalculationScenariosResponse(ApiModel):
    results: list[dict[str, object]]


class CalculatorDraftRequest(ApiModel):
    """Natural-language comparison request used to prepare, not decide, inputs."""

    question: str = Field(..., min_length=3, max_length=5000)

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("question must not be blank")
        return normalized


class CalculatorDraftEvidence(ApiModel):
    title: str = ""
    section_title: str = ""
    quote: str = ""
    source_url: str = ""


class CalculatorDraftValue(ApiModel):
    value: str
    unit: Literal["percent", "vnd"]
    evidence_index: int


class CalculatorDraftResponse(ApiModel):
    question: str
    evidence: list[CalculatorDraftEvidence] = Field(default_factory=list)
    values: list[CalculatorDraftValue] = Field(default_factory=list)
    message: str = ""


class LegalTimelineDocument(ApiModel):
    document_number: str
    title: str = ""
    issued_at: str = ""
    effective_from: str = ""
    effective_to: str = ""
    status: str = ""
    source_url: str = ""
    viewer_url: str = ""
    state_at_date: Literal["not_yet_effective", "effective", "expired", "unknown"]


class LegalTimelineEvent(ApiModel):
    relation: str
    source_document_number: str
    target_document_number: str
    adverse: bool = False


class LegalTimelineResponse(ApiModel):
    query_document: LegalTimelineDocument
    as_of: str
    documents: list[LegalTimelineDocument] = Field(default_factory=list)
    events: list[LegalTimelineEvent] = Field(default_factory=list)
    degraded: bool = False


class EligibilityChecklistRequest(ApiModel):
    topic: Literal["benefit", "five_year", "referral", "emergency", "student_contribution"]
    facts: dict[str, str | bool | int | float | None] = Field(default_factory=dict, max_length=32)
    conversation_id: str = Field(default="", max_length=128)

    @field_validator("facts")
    @classmethod
    def bound_fact_values(
        cls, value: dict[str, str | bool | int | float | None]
    ) -> dict[str, str | bool | int | float | None]:
        if any(len(key) > 64 for key in value):
            raise ValueError("fact keys must be at most 64 characters")
        if any(isinstance(item, str) and len(item) > 500 for item in value.values()):
            raise ValueError("fact values must be at most 500 characters")
        return value


class EligibilityChecklistField(ApiModel):
    key: str
    label: str
    reason: str
    input_type: Literal["text", "date", "number", "boolean", "select"]
    options: list[str] = Field(default_factory=list)


class EligibilityChecklistResponse(ApiModel):
    topic: str
    complete: bool
    missing: list[EligibilityChecklistField] = Field(default_factory=list)
    accepted_fact_keys: list[str] = Field(default_factory=list)
    next_question: str = ""
    legal_retrieval_required: bool = True
    conversation_id: str = ""
    facts_persisted: bool = False


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


class VbplDiscoveryItem(ApiModel):
    """Một VBPL từ danh sách phát hiện mới nhất."""
    model_config = ConfigDict(extra="allow")

    doc_id: str
    title: str = ""
    so_ky_hieu: str = ""
    issue_date: str = ""
    issuing_body: str = ""
    doc_type: str = ""
    legal_status: str = ""
    summary: str = ""
    is_health_related: bool = False
    ingestion_status: Literal["not_imported", "queued", "running", "succeeded", "partial", "failed"] = "not_imported"


class VbplDiscoveryResponse(ApiModel):
    items: list[VbplDiscoveryItem] = Field(default_factory=list)
    last_synced_at: datetime | None = None
    stale: bool = True
    refresh_status: Literal["idle", "queued", "running", "succeeded", "failed"] = "idle"


class VbplRefreshResponse(ApiModel):
    refresh_id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    poll_url: str = ""


class VbplStage(ApiModel):
    stage: Literal["database", "embedding", "relationships"]
    status: Literal["pending", "running", "succeeded", "failed", "skipped"]
    attempt: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    error_code: str = ""
    error: str = ""
    retryable: bool = False


class VbplIngestItem(ApiModel):
    doc_id: str
    status: Literal["queued", "running", "succeeded", "partial", "failed"]
    current_stage: Literal["database", "embedding", "relationships"]
    chunks_count: int = 0
    error: str = ""
    stages: list[VbplStage] = Field(default_factory=list)


class VbplIngestJob(ApiModel):
    job_id: str
    dataset_id: str
    status: Literal["queued", "running", "succeeded", "partial", "failed"]
    requested_by: str = ""
    trigger: Literal["manual", "daily", "retry"] = "manual"
    total_items: int = 0
    succeeded_items: int = 0
    failed_items: int = 0
    error: str = ""
    poll_url: str = ""
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    items: list[VbplIngestItem] = Field(default_factory=list)


class VbplIngestAccepted(ApiModel):
    job_id: str
    dataset_id: str
    status: Literal["queued", "running", "succeeded", "partial", "failed"]
    poll_url: str = ""


class VbplSyncStatus(ApiModel):
    refresh_id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    poll_url: str = ""
    error: str = ""
    items_count: int = 0
    last_synced_at: datetime | None = None


class VbplRetryRequest(ApiModel):
    from_stage: Literal["database", "embedding", "relationships"] | None = None


class VbplDocumentDetail(ApiModel):
    """Chi tiết VBPL từ MOJ Gateway."""
    model_config = ConfigDict(extra="allow")

    doc_id: str
    title: str = ""
    so_ky_hieu: str = ""
    content_html: str = ""
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class VbplIngestRequest(ApiModel):
    """Request body để ingest VBPL vào kho tri thức."""
    doc_ids: list[str] = Field(..., min_length=1, max_length=10)


class VbplIngestResult(ApiModel):
    """Kết quả ingest từng VBPL."""
    doc_id: str
    status: Literal["success", "partial", "failed"]
    supabase: Literal["success", "skipped", "failed"] = "skipped"
    qdrant: Literal["success", "skipped", "failed"] = "skipped"
    neo4j: Literal["success", "skipped", "failed"] = "skipped"
    error: str = ""
    chunks_count: int = 0


class VbplIngestResponse(ApiModel):
    """Response cho batch ingest."""
    results: list[VbplIngestResult] = Field(default_factory=list)
    total: int = 0
    success: int = 0
    failed: int = 0


class AgentStatusResponse(ApiModel):
    status: str = Field(..., description="Trạng thái agent.")
    agent: str = Field(..., description="Tên và phiên bản agent.")


class ErrorResponse(ApiModel):
    code: str = Field(..., description="Mã lỗi ổn định cho client.")
    message: str = Field(..., description="Thông báo lỗi an toàn cho người dùng.")
    request_id: str = Field(..., description="ID dùng để tra log server-side.")
    retryable: bool = Field(default=False, description="Client có thể retry với cùng idempotency key.")


class ReadinessResponse(ApiModel):
    status: str
    database: bool
    qdrant: bool
    neo4j: bool
    llm: bool
    embedding: bool
    details: dict[str, Any] = Field(default_factory=dict)
