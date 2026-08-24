from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache

from src.agents.prompts import NO_EVIDENCE_RESPONSE
from src.agents.state import AgentState
from src.config import get_settings
from src.models.graph import Citation, Entity, Relation, RetrievalResult
from src.services.chat import get_runtime
from src.services.claims import build_legal_claim, claim_dict
from src.services.retrieval import (
    decompose_query,
    no_answer_response,
    requires_evidence_verification,
    retrieval_intent,
)

_REASONING_BLOCK = re.compile(
    r"<\s*(?:thinking|analysis|chain_of_thought|reasoning)\b[^>]*>.*?"
    r"<\s*/\s*(?:thinking|analysis|chain_of_thought|reasoning)\s*>",
    flags=re.IGNORECASE | re.DOTALL,
)
_INTERNAL_CONTEXT_FIELD = re.compile(
    r"\b(?:EVIDENCE_ID|DOCUMENT_ID|DATASET|DOCUMENT|CHUNK|SOURCE_REF)\s*=\s*[^\s,;]+",
    flags=re.IGNORECASE,
)
_CLAIM_TOKEN = re.compile(r"[0-9A-Za-zÀ-ỹĐđ]+", flags=re.IGNORECASE)
_FACT_NUMBER = re.compile(r"\d+(?:[./%-]\d+)*", re.IGNORECASE)
_STATUS_POLARITIES = (
    ("hết hiệu lực", "còn hiệu lực"),
    ("không còn hiệu lực", "còn hiệu lực"),
    ("bãi bỏ", "còn hiệu lực"),
    ("thay thế", "còn hiệu lực"),
)
_CLAIM_STOPWORDS = {
    "và", "là", "có", "được", "cho", "của", "theo", "trong", "với", "từ", "này",
    "khi", "để", "một", "các", "những", "về", "không", "người", "việc", "tại", "đến",
    "thì", "bị", "sẽ", "đã", "hay", "hoặc", "nếu", "cần", "phải", "nên", "được",
}
_HIGH_RISK_MARKERS = (
    "hiệu lực", "bãi bỏ", "thay thế", "mức hưởng", "mức chi trả", "được chi trả",
    "bao nhiêu tiền", "thanh toán",
)
_OFFICIAL_STATUS_MARKERS = ("hiệu lực", "còn hiệu lực", "hết hiệu lực", "bãi bỏ", "thay thế")


async def intake_node(state: AgentState) -> dict:
    query = state.get("query", "").strip()
    if not query:
        return {"error": "Query must not be empty"}
    return {"query": query}


async def extract_entities_node(state: AgentState) -> dict:
    query = state.get("query", "")
    return {"entities": [Entity(name=query, entity_type="query")] if query else []}


async def retrieve_vectors_node(state: AgentState) -> dict:
    query = state.get("query", "")
    runtime = get_runtime()
    subqueries = decompose_query(query)
    # Adaptive retrieval preserves the complete user question and pairs it
    # with a clause-shaped rewrite. Running deterministic decomposition first
    # used to discard cross-condition facts (e.g. “mức đóng *và* hỗ trợ”),
    # bypassing both HyDE and the current-law reranker.
    if get_settings().query_rewrite_enabled:
        bundle = await runtime.retrieve_bundle_adaptive(query)
    elif len(subqueries) > 1:
        bundle = await runtime.retrieve_bundle_many(subqueries)
    else:
        bundle = await runtime.retrieve_bundle(query)
    evidence, relations = bundle.evidence, bundle.relations
    return {
        "vector_results": [item for item in evidence if "semantic" in item.channels],
        "graph_results": relations,
        "retrieved_evidence": evidence,
        "response": bundle.direct_response,
        "direct_citations": bundle.direct_citations or [],
    }


def _relation_context(relation: Relation) -> str:
    # Storage/graph identifiers never enter the model context.  Relationship
    # descriptions are useful legal facts; database topology is not.
    return (
        f"QUAN HỆ PHÁP LÝ: {relation.relation_type}. "
        f"{relation.description}".strip()
    )


async def assemble_context_node(state: AgentState) -> dict:
    evidence: list[RetrievalResult] = state.get("retrieved_evidence", [])
    relations: list[Relation] = state.get("graph_results", [])
    settings = get_settings()
    context = _pack_context(
        evidence,
        relations,
        settings.max_context_chars,
        token_budget=settings.max_context_tokens,
        model=settings.model_name,
    )
    return {"context": context}


def _pack_context(
    evidence: Sequence[RetrievalResult],
    relations: Sequence[Relation],
    budget: int,
    *,
    token_budget: int | None = None,
    model: str = "",
) -> str:
    """Pack complete evidence blocks until the context budget is exhausted.

    A block is either included in full or omitted (except for the bounded
    per-passage excerpt), so the final character budget cannot cut a citation
    in the middle and make its source span ambiguous.
    """
    if budget <= 0:
        return ""
    parts: list[str] = []
    used = 0
    used_tokens = 0
    for index, item in enumerate(evidence, start=1):
        metadata_lines = []
        if item.document_type:
            metadata_lines.append(f"LOẠI VĂN BẢN: {item.document_type}")
        if item.effective_from:
            metadata_lines.append(f"HIỆU LỰC TỪ: {item.effective_from}")
        if item.effective_to:
            metadata_lines.append(f"HIỆU LỰC ĐẾN: {item.effective_to}")
        if item.legal_status_verified and item.legal_status:
            metadata_lines.append(f"TÌNH TRẠNG ĐÃ KIỂM TRA: {item.legal_status}")
        metadata = "\n".join(metadata_lines)
        if metadata:
            metadata += "\n"
        block = (
            f"NGUỒN THỨ {index}\n"
            f"ƯU TIÊN NGỮ CẢNH: {index}\n"
            f"TÊN VĂN BẢN: {item.title}\n"
            f"SỐ/KÝ HIỆU CÔNG KHAI: {item.document_number}\n"
            f"{metadata}"
            f"ĐIỀU/MỤC: {item.section_title}\n"
            f"NỘI DUNG: {item.content[:2000]}"
        )
        separator = "\n---\n" if parts else ""
        block_tokens = _count_tokens(block, model) if token_budget is not None else 0
        separator_tokens = _count_tokens(separator, model) if token_budget is not None else 0
        if used + len(separator) + len(block) > budget or (
            token_budget is not None and used_tokens + separator_tokens + block_tokens > token_budget
        ):
            break
        parts.append(block)
        used += len(separator) + len(block)
        used_tokens += separator_tokens + block_tokens
    for relation in relations:
        block = _relation_context(relation)
        separator = "\n---\n" if parts else ""
        block_tokens = _count_tokens(block, model) if token_budget is not None else 0
        separator_tokens = _count_tokens(separator, model) if token_budget is not None else 0
        if used + len(separator) + len(block) > budget or (
            token_budget is not None and used_tokens + separator_tokens + block_tokens > token_budget
        ):
            break
        parts.append(block)
        used += len(separator) + len(block)
        used_tokens += separator_tokens + block_tokens
    return "\n---\n".join(parts)


@lru_cache(maxsize=8)
def _get_encoder(model: str):
    try:
        import tiktoken

        return tiktoken.encoding_for_model(model or "gpt-4o-mini")
    except Exception:
        return None


def _count_tokens(value: str, model: str) -> int:
    encoder = _get_encoder(model)
    if encoder is None:
        return max(1, (len(value) + 3) // 4)
    return len(encoder.encode(value))


async def verify_evidence_node(state: AgentState) -> dict:
    """Fail closed for high-risk claims when no release-scoped evidence survived retrieval."""
    query = state.get("query", "")
    evidence: list[RetrievalResult] = state.get("retrieved_evidence", [])
    direct_citations: list[Citation] = state.get("direct_citations", [])
    if not requires_evidence_verification(query):
        return {"verification_failed": False}
    valid = [
        item for item in evidence
        if item.dataset_id and item.document_id and item.source_start is not None and item.source_end is not None
    ]
    official_status = any(
        citation.evidence_kind == "document_metadata" and citation.provenance_verified
        for citation in direct_citations
    )
    if any(marker in query.casefold() for marker in _OFFICIAL_STATUS_MARKERS) and not official_status:
        return {"verification_failed": True, "response": no_answer_response(query, reason="unverified")}
    if valid or official_status:
        return {"verification_failed": False}
    return {"verification_failed": True, "response": no_answer_response(query, reason="unverified")}


async def generate_node(state: AgentState) -> dict:
    if state.get("response"):
        return {"response": state["response"]}
    evidence: list[RetrievalResult] = state.get("retrieved_evidence", [])
    if not evidence:
        return {"response": no_answer_response(state.get("query", ""))}
    # Legal-unit enumeration is extractive: render canonical labelled units
    # directly instead of spending an LLM call (and risking reordering or
    # inventing a missing item).  The guardrail still audits the resulting
    # claims and emits the same citation contract.
    if retrieval_intent(state.get("query", "")) == "legal_unit":
        return {"response": _deterministic_legal_unit_response(evidence)}
    response = await get_runtime().generate(state.get("query", ""), state.get("context", ""))
    return {"response": response}


def _deterministic_legal_unit_response(evidence: Sequence[RetrievalResult]) -> str:
    """Format enumerated legal units without an LLM round trip.

    Only source-backed text is rendered, with stable ordering and a hard
    output bound.  Duplicate units are removed by unit/chunk identity.
    """
    lines: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(evidence, start=1):
        identity = item.unit_id or item.chunk_id
        if not identity or identity in seen:
            continue
        text = " ".join(item.content.split())
        if not text:
            continue
        seen.add(identity)
        label = " ".join((item.section_title or "").split())
        if not label:
            label = f"Nội dung {index}"
        lines.append(f"- {label}: {text[:900]}")
        if len(lines) >= 8:
            break
    if not lines:
        return no_answer_response(reason="no_evidence")
    return "Các điều/khoản được nguồn pháp lý xác nhận:\n" + "\n".join(lines)


def _sanitize_output(value: str, evidence: Sequence[RetrievalResult] = ()) -> str:
    sanitized = _REASONING_BLOCK.sub("", value).strip()
    sanitized = re.sub(r"^\s*(?:<\/?(?:thinking|analysis|reasoning)>)+\s*", "", sanitized, flags=re.I)
    sanitized = _INTERNAL_CONTEXT_FIELD.sub("", sanitized)
    # Defence in depth for stale cached/provider output that copied an opaque
    # identifier in prose. Replace exact tokens only, never substrings of a
    # public legal number, date, percentage or monetary value.
    replacements: dict[str, str] = {}
    for item in evidence:
        public_label = item.document_number or item.title or "nguồn pháp lý"
        if len(item.document_id) >= 5:
            replacements[item.document_id] = public_label
        if len(item.chunk_id) >= 5:
            replacements[item.chunk_id] = "nguồn pháp lý"
        if len(item.dataset_id) >= 5:
            replacements[item.dataset_id] = ""
    for private_id in sorted(replacements, key=len, reverse=True):
        sanitized = re.sub(
            rf"(?<![\w./-]){re.escape(private_id)}(?![\w./-])",
            replacements[private_id],
            sanitized,
        )
    return sanitized.strip()


def _citations_from_evidence(evidence: list[RetrievalResult]) -> list[Citation]:
    from src.config import get_settings

    citations: list[Citation] = []
    seen: set[str] = set()
    ranked = sorted(
        evidence,
        key=lambda item: (-float(item.score), str(item.chunk_id)),
    )
    for item in ranked:
        if not item.chunk_id or item.chunk_id in seen:
            continue
        seen.add(item.chunk_id)
        citations.append(
            Citation(
                document_id=item.document_id,
                chunk_id=item.chunk_id,
                dataset_id=item.dataset_id,
                title=item.title,
                document_number=item.document_number,
                section_title=item.section_title,
                quote=item.content[:600],
                channels=item.channels,
                evidence_kind="legal_unit" if "page_index" in item.channels else "passage",
                source_start=item.source_start,
                source_end=item.source_end,
                text_sha256=item.text_sha256,
                provenance_verified=item.legal_status_verified,
                source_url=item.source_url,
                source_checked_at=item.source_checked_at,
            )
        )
        if len(citations) >= get_settings().max_citations:
            break
    return citations


def _claim_tokens(value: str) -> set[str]:
    return {
        token.casefold()
        for token in _CLAIM_TOKEN.findall(value)
        if token.casefold() not in _CLAIM_STOPWORDS and len(token) > 1
    }


def _claim_facts_supported(claim: str, evidence: Sequence[str]) -> bool:
    """Reject concrete numeric/status contradictions in a cited claim.

    Token overlap can accept a sentence with a changed date, percentage, or
    legal-status polarity. This bounded deterministic check is conservative;
    it strengthens the lexical audit without pretending to be open-ended
    semantic proof.
    """
    claim_text = claim.casefold()
    evidence_text = " ".join(evidence).casefold()
    if not set(_FACT_NUMBER.findall(claim_text)).issubset(set(_FACT_NUMBER.findall(evidence_text))):
        return False
    for positive, negative in _STATUS_POLARITIES:
        if positive in claim_text and negative in evidence_text and positive not in evidence_text:
            return False
        if negative in claim_text and positive in evidence_text and negative not in evidence_text:
            return False
    return True


def _audit_claims(response: str, citations: Sequence[Citation], query: str = "") -> list[dict]:
    """Create a conservative claim-to-evidence audit without trusting the LLM.

    This is a conservative lexical/fact entailment pre-check, not an
    open-ended semantic proof. High-risk routes fail closed when a claim cannot
    be tied to citation overlap or its concrete number/status conflicts with
    evidence; a stronger model verifier can be added without changing the
    response contract.
    """
    sentences = [
        sentence.strip(" -*•\t")
        for sentence in re.split(r"(?<=[.!?。！？])\s+|\n+", response)
        if sentence.strip(" -*•\t")
    ]
    source_text = {
        citation.chunk_id: " ".join((citation.title, citation.section_title, citation.quote)).casefold()
        for citation in citations
    }
    source_tokens = {citation_id: _claim_tokens(value) for citation_id, value in source_text.items()}
    claims: list[dict] = []
    for index, sentence in enumerate(sentences, start=1):
        tokens = _claim_tokens(sentence)
        best_id = ""
        best_overlap = 0
        for citation_id, evidence_tokens in source_tokens.items():
            if not _claim_facts_supported(sentence, [source_text[citation_id]]):
                continue
            overlap = len(tokens & evidence_tokens)
            if overlap > best_overlap:
                best_id, best_overlap = citation_id, overlap
        risk_markers = [marker for marker in _HIGH_RISK_MARKERS if marker in sentence.casefold()]
        requires_official_status = any(marker in query.casefold() for marker in _OFFICIAL_STATUS_MARKERS)
        official_status_supported = any(
            citation.evidence_kind == "document_metadata" and citation.provenance_verified
            for citation in citations
        )
        risk_supported = not risk_markers or any(
            all(marker in value for marker in risk_markers) for value in source_text.values()
        )
        if requires_official_status and risk_markers:
            risk_supported = risk_supported and official_status_supported
        if not tokens:
            verification, reason = "unsupported", "claim has no verifiable content"
        elif not risk_supported:
            verification, reason = "unsupported", "high-risk marker is absent from cited evidence"
        elif not best_id or not _claim_facts_supported(sentence, [source_text[best_id]]):
            verification, reason = "unsupported", "facts are not supported by one cited source"
        elif best_overlap >= 2:
            verification, reason = "entailed", "lexical overlap with cited evidence"
        elif best_overlap == 1:
            verification, reason = "partial", "limited lexical overlap; review required"
        else:
            verification, reason = "unsupported", "no cited evidence overlap"
        best_citation = next(
            (citation for citation in citations if citation.chunk_id == best_id),
            None,
        )
        claims.append(
            claim_dict(
                build_legal_claim(
                    claim_id=f"claim-{index}",
                    text=sentence,
                    citation=best_citation,
                    verification=verification,
                    reason=reason,
                )
            )
        )
    return claims


def _retain_supported_claims(claims: Sequence[dict]) -> tuple[str, list[dict]]:
    """Keep only sentences tied to a concrete citation by the audit.

    Returning raw retrieval excerpts after a verification failure both leaks
    internal vocabulary and turns a ranking error into a misleading answer.
    Partial/unsupported sentences are therefore removed independently; if no
    sentence survives, the system abstains cleanly.
    """
    supported = [claim for claim in claims if claim.get("verification") == "entailed"]
    if not supported:
        return NO_EVIDENCE_RESPONSE, []
    return "\n".join(
        f"- {str(claim.get('text') or '').strip().strip('*_')}" for claim in supported
    ), supported


def _normalize_response(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Sequence):
        return "".join(
            str(block.get("text", ""))
            for block in value
            if isinstance(block, dict) and block.get("type") == "text"
        ).strip()
    return ""


async def guardrail_node(state: AgentState) -> dict:
    evidence = state.get("retrieved_evidence", [])
    response = _sanitize_output(_normalize_response(state.get("response", "")), evidence)
    if not response:
        response = NO_EVIDENCE_RESPONSE
    citations = state.get("direct_citations") or _citations_from_evidence(evidence)
    claims = _audit_claims(response, citations, state.get("query", ""))
    if evidence and any(
        claim["verification"] != "entailed" for claim in claims
    ):
        response, claims = _retain_supported_claims(claims)
    # An abstention is a statement about the absence of sufficient support.
    # Showing a residual, unrelated citation next to it is internally
    # contradictory and makes a failed retrieval look authoritative.
    if response == NO_EVIDENCE_RESPONSE:
        citations = []
        claims = []
    supported_ids = {
        evidence_id
        for claim in claims
        if claim.get("verification") == "entailed"
        for evidence_id in claim.get("evidence_ids", [])
    }
    if evidence:
        citations = [citation for citation in citations if citation.chunk_id in supported_ids]
    return {
        "response": response,
        "citations": [citation.model_dump() for citation in citations],
        "claims": claims,
    }
