"""Bounded, owner-scoped query resolution for conversational references.

Conversation turns are context hints only. The resolved query is sent back
through the normal active-release retrieval path; stored assistant text and
internal IDs are never treated as evidence.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from src.services.eligibility_checklist import FIELD_CATALOG

_REFERENCE_MARKER = re.compile(
    r"\b(văn bản đó|văn bản trên|văn bản này|khoản trên|điều trên|mục trên|nội dung đó|quy định đó)\b",
    re.IGNORECASE,
)
_SIGNATURE = re.compile(
    r"\b\d{1,6}/(?:\d{4}/)?[A-ZĐÐ][A-ZĐÐ0-9./-]*\b", re.IGNORECASE
)
_UNTRUSTED_HINT = re.compile(
    r"(?i)(?:ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions?|"
    r"system\s+prompt|api\s*key|access\s*token|evidence[_\s-]*id|"
    r"<\s*(?:system|thinking|tool|script)\b)"
)


def build_conversation_anchors(citations: Sequence[Mapping[str, object]]) -> list[dict[str, str]]:
    """Extract a small typed anchor set from citations, never from free text."""
    anchors: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for citation in citations:
        if not isinstance(citation, Mapping):
            continue
        title = str(citation.get("title") or "").strip()
        quote = str(citation.get("quote") or "").strip()
        signature_match = _SIGNATURE.search(f"{title} {quote}")
        signature = signature_match.group(0) if signature_match else ""
        document_id = str(citation.get("document_id") or "").strip()
        dataset_id = str(citation.get("dataset_id") or "").strip()
        key = (document_id, signature or title)
        if not key[0] and not key[1] or key in seen:
            continue
        seen.add(key)
        anchors.append(
            {
                "document_id": document_id,
                "signature": signature,
                "title": title[:240],
                "dataset_id": dataset_id,
            }
        )
        if len(anchors) >= 8:
            break
    return anchors


def _citation_hint(citation: Mapping[str, object]) -> str:
    title = str(citation.get("title") or "").strip()
    quote = str(citation.get("quote") or "").strip()
    signature_value = str(citation.get("signature") or "").strip()
    match = _SIGNATURE.search(f"{signature_value} {title} {quote}")
    signature = match.group(0) if match else ""
    # Stored citations are owner-scoped, but their title/quote can still be
    # poisoned by an imported corpus or an old client.  Only a legal
    # signature survives an instruction-like hint; free text is never allowed
    # to become a prompt fragment in the next retrieval query.
    if title and not _UNTRUSTED_HINT.search(title):
        return f"{signature} {title}".strip()[:320]
    return signature[:120]


def resolve_conversational_query(query: str, turns: Sequence[Mapping[str, object]] = ()) -> str:
    """Resolve a bounded reference from the newest owner-scoped turn."""
    normalized = query.strip()
    if not normalized or not _REFERENCE_MARKER.search(normalized):
        return normalized
    for turn in reversed(list(turns)[-20:]):
        anchors = turn.get("anchors")
        if isinstance(anchors, Sequence) and not isinstance(anchors, (str, bytes)):
            for anchor in anchors:
                if isinstance(anchor, Mapping):
                    hint = _citation_hint(anchor)
                    if hint:
                        return f"{normalized} Văn bản tham chiếu: {hint}."[:700]
        citations = turn.get("citations")
        if not isinstance(citations, Sequence) or isinstance(citations, (str, bytes)):
            continue
        for citation in citations:
            if isinstance(citation, Mapping):
                hint = _citation_hint(citation)
                if hint:
                    return f"{normalized} Văn bản tham chiếu: {hint}."[:700]
    return normalized


def apply_structured_user_facts(
    query: str, turns: Sequence[Mapping[str, object]] = ()
) -> str:
    """Append only allowlisted, bounded user facts as non-evidence context."""
    normalized = query.strip()
    if not normalized:
        return normalized
    facts: Mapping[str, object] = {}
    for turn in reversed(list(turns)[-20:]):
        candidate = turn.get("user_facts")
        if isinstance(candidate, Mapping):
            facts = candidate
            break
    fragments: list[str] = []
    for key in sorted(set(facts) & set(FIELD_CATALOG)):
        value = facts[key]
        if isinstance(value, bool):
            public_value = "có" if value else "không"
        elif isinstance(value, (str, int, float)):
            public_value = " ".join(str(value).split())[:120]
            if _UNTRUSTED_HINT.search(public_value):
                continue
            public_value = re.sub(r"[^0-9A-Za-zÀ-ỹĐđ .,/()%+\-]", "", public_value).strip()
        else:
            continue
        if public_value:
            fragments.append(f"{FIELD_CATALOG[key].label}: {public_value}")
    if not fragments:
        return normalized
    suffix = "; ".join(fragments)[:800]
    return (
        f"{normalized}\nTình tiết do người dùng xác nhận, chỉ dùng để chọn quy định; "
        f"không phải căn cứ pháp lý: {suffix}."
    )[:1500]
