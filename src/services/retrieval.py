"""Deterministic planning, safety routing and channel fusion for Legal RAG."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence

from src.models.graph import RetrievalResult

_DOCUMENT_NUMBER = re.compile(r"\b\d{1,4}/\d{4}/[A-ZĐ0-9-]+\b", re.IGNORECASE)
_LEGAL_LABEL = re.compile(r"\b(?:điều|khoản)\s+\d+[a-zđ]?|\b[a-zđ]\)", re.IGNORECASE)


def extract_document_numbers(query: str) -> list[str]:
    return sorted({match.group(0).upper() for match in _DOCUMENT_NUMBER.finditer(query)})


def extract_legal_labels(query: str) -> list[str]:
    return sorted({match.group(0) for match in _LEGAL_LABEL.finditer(query)}, key=str.casefold)


def retrieval_intent(query: str) -> str:
    lowered = query.casefold()
    if _LEGAL_LABEL.search(query):
        return "legal_unit"
    if extract_document_numbers(query):
        return "lookup"
    if any(token in lowered for token in ("hiệu lực", "từ ngày", "trước ngày", "thay thế", "bãi bỏ")):
        return "temporal"
    if any(token in lowered for token in ("liên quan", "dẫn chiếu", "sửa đổi", "căn cứ")):
        return "relational"
    return "thematic"


def is_metadata_question(query: str) -> bool:
    lowered = query.casefold()
    return bool(extract_document_numbers(query)) and any(
        token in lowered
        for token in (
            "tiêu đề", "tên văn bản", "tên của", "hiệu lực", "ban hành", "loại văn bản",
            "danh mục", "category", "tình trạng", "số ký hiệu", "số hiệu",
        )
    )


def policy_response(query: str) -> str | None:
    """Return a deterministic safe response before any external retrieval."""
    lowered = query.casefold()
    if any(token in lowered for token in ("bỏ qua hướng dẫn", "ignore previous", "system prompt", "prompt nội bộ")):
        return "Tôi không thể làm theo yêu cầu thay đổi hướng dẫn hệ thống. Tôi chỉ có thể hỗ trợ câu hỏi BHYT và viện phí dựa trên nguồn hợp lệ."
    if any(token in lowered for token in ("otp", "cvv", "mật khẩu", "số thẻ", "cccd", "hồ sơ bệnh án", "bệnh án của")):
        return "Không gửi thông tin định danh, OTP, CVV hoặc hồ sơ bệnh án. Tôi có thể hướng dẫn quy trình chung nếu bạn đã ẩn dữ liệu cá nhân."
    if any(token in lowered for token in ("kê đơn", "uống thuốc", "chẩn đoán bệnh", "liều thuốc")):
        return "Tôi không thể chẩn đoán hoặc kê đơn. Với triệu chứng hay liều dùng, hãy liên hệ bác sĩ hoặc cơ sở y tế; tôi chỉ hỗ trợ thông tin chính sách và viện phí có nguồn."
    if "viện phí" in lowered and any(token in lowered for token in ("bao nhiêu", "ước tính", "tổng tiền")):
        return "Để đối chiếu viện phí an toàn, cần bảng kê chi tiết, nơi khám, tuyến/chuyển tuyến, mức hưởng BHYT và thời điểm điều trị. Không nên kết luận số tiền khi thiếu các đầu vào này."
    return None


def weighted_rrf(channel_hits: dict[str, Sequence[RetrievalResult]], *, limit: int) -> list[RetrievalResult]:
    """Fuse channel ranks while preserving raw scores and evidence provenance."""
    weights = {"exact": 2.0, "lexical": 1.15, "semantic": 1.0, "legal_graph": 0.7, "page_index": 1.35}
    aggregate: defaultdict[str, float] = defaultdict(float)
    selected: dict[str, RetrievalResult] = {}
    details: defaultdict[str, dict[str, float]] = defaultdict(dict)
    for channel, hits in channel_hits.items():
        weight = weights.get(channel, 1.0)
        for rank, hit in enumerate(hits, start=1):
            aggregate[hit.chunk_id] += weight / (60 + rank)
            details[hit.chunk_id][f"{channel}_rank"] = float(rank)
            details[hit.chunk_id][f"{channel}_raw_score"] = float(hit.score)
            existing = selected.get(hit.chunk_id)
            if existing is None or hit.score > existing.score:
                selected[hit.chunk_id] = hit.model_copy(deep=True)
            selected[hit.chunk_id].channels = sorted(set([*selected[hit.chunk_id].channels, channel]))
    ordered = sorted(selected, key=lambda identifier: (-aggregate[identifier], identifier))
    diverse: list[RetrievalResult] = []
    per_document: defaultdict[str, int] = defaultdict(int)
    for identifier in ordered:
        item = selected[identifier]
        if per_document[item.document_id] >= 2:
            continue
        per_document[item.document_id] += 1
        item.score = aggregate[identifier]
        item.rank_details = {**details[identifier], "rrf_score": aggregate[identifier]}
        diverse.append(item)
        if len(diverse) >= limit:
            break
    return diverse
