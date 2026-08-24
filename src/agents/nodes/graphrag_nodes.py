from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache

from src.agents.prompts import NO_EVIDENCE_RESPONSE
from src.agents.state import AgentState
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
_INTERNAL_EVIDENCE_ID = re.compile(r"\b(?:EVIDENCE|DOCUMENT)_ID\s*=\s*[^\s,;]+", flags=re.IGNORECASE)
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
    bundle = (
        await runtime.retrieve_bundle_many(subqueries)
        if len(subqueries) > 1
        else await runtime.retrieve_bundle(query)
    )
    evidence, relations = bundle.evidence, bundle.relations
    return {
        "vector_results": [item for item in evidence if "semantic" in item.channels],
        "graph_results": relations,
        "retrieved_evidence": evidence,
        "response": bundle.direct_response,
        "direct_citations": bundle.direct_citations or [],
    }


def _relation_context(relation: Relation) -> str:
    identifiers = " -> ".join(
        part for part in (relation.source_id, relation.target_id) if part
    )
    return (
        f"GRAPH {identifiers}: {relation.source} --{relation.relation_type}--> "
        f"{relation.target}. {relation.description}".strip()
    )


async def assemble_context_node(state: AgentState) -> dict:
    evidence: list[RetrievalResult] = state.get("retrieved_evidence", [])
    relations: list[Relation] = state.get("graph_results", [])
    from src.config import get_settings

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
        block = (
            f"EVIDENCE_ID=E{index}\n"
            f"DATASET={item.dataset_id}\n"
            f"DOCUMENT={item.document_id}\n"
            f"CHUNK={item.chunk_id}\n"
            f"SECTION={item.section_title}\n"
            f"TEXT={item.content[:2000]}"
        )
        separator = "\n---\n" if parts else ""
        block_tokens = _count_tokens(block, model)
        separator_tokens = _count_tokens(separator, model)
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
        block_tokens = _count_tokens(block, model)
        separator_tokens = _count_tokens(separator, model)
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
    if _is_no_evidence_response(response):
        # The retriever has already supplied release-scoped evidence.  Do not
        # convert a model's over-cautious fallback into a false claim that no
        # evidence exists; return the verified excerpts instead.
        response = _evidence_backed_response(evidence)
    return {"response": response}


def _deterministic_legal_unit_response(evidence: Sequence[RetrievalResult]) -> str:
    """Format enumerated legal units without an LLM round trip.

    Only source-backed text is rendered, with stable ordering and a hard
    output bound.  Duplicate units are removed by unit/chunk identity.
    """
    lines: list[str] = []
    seen: set[str] = set()
    for item in evidence:
        identity = item.unit_id or item.chunk_id
        if not identity or identity in seen:
            continue
        text = " ".join(item.content.split())
        if not text:
            continue
        seen.add(identity)
        label = " ".join((item.section_title or "").split())
        if not label:
            label = item.unit_id or item.chunk_id
        lines.append(f"- {label}: {text[:900]}")
        if len(lines) >= 8:
            break
    if not lines:
        return no_answer_response(reason="no_evidence")
    return "Các điều/khoản có evidence trực tiếp:\n" + "\n".join(lines)


def _is_no_evidence_response(response: str) -> bool:
    normalized = " ".join(response.casefold().split())
    expected = " ".join(NO_EVIDENCE_RESPONSE.casefold().split())
    fallback_prefix = "hiện tại hệ thống không tìm thấy thông tin hoặc văn bản pháp lý phù hợp"
    # Some models append a partial answer after the fallback sentence.  Once
    # evidence is present, that opening is still false and must not reach the
    # user; the deterministic excerpt response below is grounded instead.
    return normalized == expected or normalized.startswith(fallback_prefix)


def _evidence_backed_response(evidence: list[RetrievalResult]) -> str:
    excerpts: list[str] = []
    for item in evidence[:3]:
        excerpt = " ".join(item.content.split())
        if not excerpt:
            continue
        label = item.section_title or item.title or item.document_id
        excerpts.append(f"- {label}: {excerpt[:700]}")
    if not excerpts:
        return NO_EVIDENCE_RESPONSE
    return (
        "Tôi đã tìm thấy các trích đoạn liên quan sau:\n"
        + "\n".join(excerpts)
        + "\n\nCác trích đoạn trên là phần thông tin có thể xác nhận từ evidence hiện có; "
        "chưa đủ cơ sở để khẳng định ngoài phạm vi đó."
    )


def _sanitize_output(value: str) -> str:
    sanitized = _REASONING_BLOCK.sub("", value).strip()
    sanitized = re.sub(r"^\s*(?:<\/?(?:thinking|analysis|reasoning)>)+\s*", "", sanitized, flags=re.I)
    sanitized = _INTERNAL_EVIDENCE_ID.sub("", sanitized)
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
                section_title=item.section_title,
                quote=item.content[:600],
                channels=item.channels,
                evidence_kind="legal_unit" if "page_index" in item.channels else "passage",
                source_start=item.source_start,
                source_end=item.source_end,
                text_sha256=item.text_sha256,
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
    source_values = list(source_text.values())
    claims: list[dict] = []
    for index, sentence in enumerate(sentences, start=1):
        tokens = _claim_tokens(sentence)
        best_id = ""
        best_overlap = 0
        for citation_id, evidence_tokens in source_tokens.items():
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
        elif not _claim_facts_supported(sentence, source_values):
            verification, reason = "unsupported", "numeric or status fact conflicts with cited evidence"
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
    response = _sanitize_output(_normalize_response(state.get("response", "")))
    if not response:
        response = NO_EVIDENCE_RESPONSE
    citations = state.get("direct_citations") or _citations_from_evidence(state.get("retrieved_evidence", []))
    claims = _audit_claims(response, citations, state.get("query", ""))
    if (
        requires_evidence_verification(state.get("query", ""))
        and any(claim["verification"] != "entailed" for claim in claims)
        and state.get("retrieved_evidence")
    ):
        response = _evidence_backed_response(state["retrieved_evidence"])
        claims = _audit_claims(response, citations, state.get("query", ""))
    return {
        "response": response,
        "citations": [citation.model_dump() for citation in citations],
        "claims": claims,
    }
