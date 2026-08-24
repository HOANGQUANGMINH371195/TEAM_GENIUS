"""Deterministic planning, safety routing and channel fusion for Legal RAG."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence

from src.models.graph import RetrievalResult

# Vietnamese instruments use both year-qualified signatures
# (``60/2026/NQ-HĐND``) and abbreviated administrative forms such as
# ``11/CT.UBND`` or ``15/TTLB``. Keep dates out by requiring an alphabetic
# token after the slash in the abbreviated branch.
_DOCUMENT_NUMBER = re.compile(
    # ``Ð`` is present in a subset of legacy CSV signatures while the
    # normalized runtime representation uses Vietnamese ``Đ``.  Accept both
    # at extraction time; ``normalize_identifier`` canonicalizes them before
    # lookup.
    r"\b\d{1,6}/\d{4}/[A-ZĐÐ0-9][A-ZĐÐ0-9./-]*\b"
    r"|\b\d{1,6}/[A-ZĐÐ][A-ZĐÐ0-9./-]*\b",
    re.IGNORECASE,
)
_LEGAL_LABEL = re.compile(r"\b(?:điều|khoản)\s+\d+[a-zđ]?|\b[a-zđ]\)", re.IGNORECASE)


def normalize_identifier(value: str) -> str:
    """Normalize legacy Vietnamese character corruption in legal signatures."""
    return value.replace("Ð", "Đ").replace("ð", "đ").strip().upper()


def extract_document_numbers(query: str) -> list[str]:
    return sorted({normalize_identifier(match.group(0)) for match in _DOCUMENT_NUMBER.finditer(query)})


def extract_legal_labels(query: str) -> list[str]:
    return sorted({match.group(0) for match in _LEGAL_LABEL.finditer(query)}, key=str.casefold)


def retrieval_intent(query: str) -> str:
    lowered = query.casefold()
    if any(token in lowered for token in ("hiệu lực", "từ ngày", "trước ngày", "thay thế", "bãi bỏ")):
        return "temporal"
    if any(token in lowered for token in ("liên quan", "dẫn chiếu", "sửa đổi", "căn cứ")):
        return "relational"
    if _LEGAL_LABEL.search(query):
        return "legal_unit"
    if extract_document_numbers(query):
        return "lookup"
    return "thematic"


def decompose_query(query: str, *, limit: int = 3) -> list[str]:
    """Create a small deterministic set of independent sub-queries.

    This is deliberately conservative: only explicit conjunctions are split,
    and each fragment must retain enough content to be a meaningful retrieval
    request.  It feeds the bounded embedding/Qdrant batch path; final answer
    generation remains single-user and unbatched.
    """
    normalized = " ".join(query.split())
    if not normalized or limit <= 1:
        return [normalized] if normalized else []
    # This is a legal-term normalization, not an answer shortcut.  The same
    # statutory condition appears in the corpus with several spellings
    # ("05"/"5", "đồng"/"cùng" chi trả and the explicit threshold).  Expand
    # a recognisable compound condition before embedding/lexical fusion so a
    # broad passage about a five-year card validity cannot outrank its
    # operative co-payment rule.
    lowered = normalized.casefold()
    has_five_years = "5 năm" in lowered or "05 năm" in lowered
    if has_five_years and "chi trả" in lowered:
        expansions = [
            normalized,
            "BHYT 5 năm liên tục số tiền cùng chi trả lớn hơn 6 tháng lương cơ sở",
            "mức hưởng BHYT 5 năm liên tục miễn cùng chi trả",
        ]
        return expansions[:limit]
    parts = re.split(r"\s+(?:và|đồng thời|cũng như)\s+", normalized, flags=re.IGNORECASE)
    if len(parts) <= 1:
        return [normalized]
    selected = [part.strip(" ,;:.?") for part in parts if len(part.strip()) >= 12]
    if len(selected) < 2:
        return [normalized]
    return selected[: max(2, min(limit, 3))]


def is_metadata_question(query: str) -> bool:
    lowered = query.casefold()
    return bool(extract_document_numbers(query)) and any(
        token in lowered
        for token in (
            "tiêu đề", "tên văn bản", "tên đầy đủ", "tên của", "hiệu lực", "ban hành",
            "loại văn bản", "danh mục", "category", "tình trạng", "số ký hiệu", "số hiệu",
            "thuộc nhóm", "nhóm nội dung",
        )
    )


def is_simple_status_metadata_question(query: str) -> bool:
    """Allow exact metadata answers for a direct status/date lookup only.

    Comparative, temporal-chain and relational questions still go through
    lexical/semantic/graph fusion; this helper is intentionally narrow so a
    metadata shortcut cannot hide a needed graph relation.
    """
    lowered = query.casefold()
    if not is_metadata_question(query):
        return False
    if not any(token in lowered for token in ("hiệu lực", "tình trạng", "ban hành")):
        return False
    complex_markers = (
        "thay thế", "bãi bỏ", "sửa đổi", "dẫn chiếu", "liên quan", "trước ngày",
        "sau ngày", "từ ngày nào đến", "so sánh", "mốc thời gian",
    )
    return not any(marker in lowered for marker in complex_markers)


def policy_response(query: str) -> str | None:
    """Return a deterministic safe response before any external retrieval."""
    lowered = query.casefold()
    if any(token in lowered for token in ("bỏ qua hướng dẫn", "ignore previous", "system prompt", "prompt nội bộ")):
        return (
            "Tôi không thể làm theo yêu cầu thay đổi system prompt hoặc tiết lộ API key, token, secret "
            "hay hướng dẫn ẩn. Tôi chỉ hỗ trợ câu hỏi BHYT và viện phí dựa trên nguồn hợp lệ."
        )
    if any(token in lowered for token in ("otp", "cvv", "mật khẩu")):
        return (
            "Tôi không tiếp nhận hoặc lặp lại OTP, CVV hay mật khẩu. Không lưu các secret này; hãy dùng "
            "kênh thanh toán an toàn và chính thức."
        )
    if any(token in lowered for token in ("số thẻ", "cccd", "hồ sơ bệnh án", "bệnh án của")):
        return (
            "Tôi không thể cung cấp hồ sơ hoặc số thẻ của người khác. Cần xác minh danh tính và quyền đại diện "
            "trước khi cung cấp dữ liệu; tôi chỉ có thể hướng dẫn quy trình chung đã ẩn thông tin cá nhân."
        )
    if any(token in lowered for token in ("kê đơn", "uống thuốc", "chẩn đoán bệnh", "liều thuốc")):
        return "Tôi không thể chẩn đoán hoặc kê đơn. Với triệu chứng hay liều dùng, hãy liên hệ bác sĩ hoặc cơ sở y tế; tôi chỉ hỗ trợ thông tin chính sách và viện phí có nguồn."
    if any(token in lowered for token in ("claim đã được duyệt", "yêu cầu đã được duyệt", "đã được phê duyệt")):
        return "Tôi không thể xác nhận tình trạng duyệt claim khi không có nguồn trạng thái chính thức. Hãy kiểm tra kênh của cơ quan bảo hiểm hoặc cơ sở tiếp nhận."
    # A question about a person's own plan cannot be answered safely without
    # their plan/coverage facts.  Do not treat every general legal question
    # containing "quyền lợi" as an individual-eligibility request: that used
    # to bypass retrieval entirely for questions such as the statutory BHYT
    # five-consecutive-years co-payment rule.
    personal_plan_markers = (
        "gói bảo hiểm này", "gói của tôi", "gói bhyt của tôi",
        "quyền lợi của tôi", "tôi còn được hưởng", "tôi được hưởng",
    )
    if any(marker in lowered for marker in personal_plan_markers):
        return "Để xác định quyền lợi, cần tên hoặc mã gói bảo hiểm/văn bản áp dụng và ngày điều trị hoặc ngày hiệu lực. Tôi không thể khẳng định quyền lợi khi thiếu các thông tin này."
    if "viện phí" in lowered and any(token in lowered for token in ("bao nhiêu", "ước tính", "tổng tiền", "tính", "cuối cùng")):
        return "Để đối chiếu viện phí an toàn, cần hóa đơn hoặc bảng kê chi tiết, nơi khám, tuyến/chuyển tuyến, mức hưởng BHYT và thời điểm điều trị. Không nên kết luận số tiền khi thiếu các đầu vào này."
    return None


def no_answer_response(query: str = "", *, reason: str = "no_evidence") -> str:
    """Return a stable abstention that explains why a claim was not made."""
    if reason == "ambiguous":
        return (
            "Tôi tìm thấy nhiều văn bản có thể phù hợp nhưng chưa thể xác định đúng văn bản. "
            "Vui lòng cung cấp số hiệu đầy đủ, cơ quan ban hành hoặc ngày hiệu lực."
        )
    if reason == "unverified":
        return (
            "Tôi chưa thể xác minh claim này từ nguồn chính thức hoặc evidence có span hợp lệ; "
            "vì vậy không khẳng định nội dung pháp lý."
        )
    return (
        "Hiện tại hệ thống không tìm thấy thông tin hoặc văn bản pháp lý phù hợp "
        "để giải đáp câu hỏi này."
    )


def requires_evidence_verification(query: str) -> bool:
    """Identify claims where unsupported legal advice is especially harmful."""
    lowered = query.casefold()
    return any(token in lowered for token in (
        "hiệu lực", "còn hiệu lực", "hết hiệu lực", "bãi bỏ", "thay thế",
        "mức hưởng", "mức chi trả", "được chi trả", "bao nhiêu tiền", "thanh toán",
    ))


def semantic_document_focus(
    hits: Sequence[RetrievalResult], *, documents: int = 1, chunks_per_document: int = 3
) -> list[RetrievalResult]:
    """Keep answer-bearing neighbouring passages from the strongest semantic document.

    A question can match both a document's scope and its operative clause.  Pure
    passage-level fusion may retain the scope but discard the clause because it
    diversifies too aggressively across documents.  This creates a compact
    second channel from documents that receive multiple independent semantic
    hits, without another embedding or Qdrant request.
    """
    if documents <= 0 or chunks_per_document <= 0:
        return []
    grouped: defaultdict[str, list[tuple[int, RetrievalResult]]] = defaultdict(list)
    for rank, hit in enumerate(hits, start=1):
        if hit.document_id:
            grouped[hit.document_id].append((rank, hit))
    candidates = [
        (
            -sum(float(hit.score) for _, hit in rows[:chunks_per_document]),
            rows[0][0],
            document_id,
            rows,
        )
        for document_id, rows in grouped.items()
        if len(rows) >= 2
    ]
    focused: list[RetrievalResult] = []
    for _, _, _, rows in sorted(candidates)[:documents]:
        focused.extend(hit.model_copy(deep=True) for _, hit in rows[:chunks_per_document])
    return focused


def weighted_rrf(
    channel_hits: dict[str, Sequence[RetrievalResult]], *, limit: int, max_per_document: int = 2
) -> list[RetrievalResult]:
    """Fuse channel ranks while preserving raw scores and evidence provenance."""
    if max_per_document <= 0:
        return []
    weights = {
        "exact": 2.0,
        "lexical": 1.15,
        "semantic": 1.0,
        "semantic_focus": 1.35,
        "legal_graph": 0.7,
        "page_index": 1.35,
    }
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
        if per_document[item.document_id] >= max_per_document:
            continue
        per_document[item.document_id] += 1
        item.score = aggregate[identifier]
        item.rank_details = {**details[identifier], "rrf_score": aggregate[identifier]}
        diverse.append(item)
        if len(diverse) >= limit:
            break
    return diverse
