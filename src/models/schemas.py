from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(
        ...,
        min_length=1,
        max_length=5000,
        description="Tin nhắn hoặc câu hỏi từ user.",
        examples=["Quyền lợi BHYT khi khám trái tuyến là gì?"],
    )


class AnalyzeRequest(BaseModel):
    message: str = Field(
        ...,
        min_length=1,
        max_length=5000,
        description="Nội dung cần phân tích.",
        examples=["Hóa đơn có khoản thu nào cần kiểm tra?"],
    )


class ChatResponse(BaseModel):
    response: str = Field(..., description="Phản hồi từ agent.")
    analysis: str = Field(default="", description="Tóm tắt phân tích của agent.")


class AnalyzeResponse(BaseModel):
    analysis: str = Field(..., description="Kết quả phân tích input.")


class AgentStatusResponse(BaseModel):
    status: str = Field(..., description="Trạng thái agent.")
    agent: str = Field(..., description="Tên và phiên bản agent.")
