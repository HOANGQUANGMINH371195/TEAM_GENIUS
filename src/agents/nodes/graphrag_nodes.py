from __future__ import annotations

import re
from collections.abc import Sequence

from src.agents.prompts import NO_EVIDENCE_RESPONSE
from src.agents.state import AgentState
from src.models.graph import Citation, Entity, Relation, RetrievalResult
from src.services.chat import get_runtime
from src.services.retrieval import requires_evidence_verification

_REASONING_BLOCK = re.compile(
    r"<\s*(?:thinking|analysis|chain_of_thought|reasoning)\b[^>]*>.*?"
    r"<\s*/\s*(?:thinking|analysis|chain_of_thought|reasoning)\s*>",
    flags=re.IGNORECASE | re.DOTALL,
)


async def intake_node(state: AgentState) -> dict:
    query = state.get("query", "").strip()
    if not query:
        return {"error": "Query must not be empty"}
    return {"query": query}


async def extract_entities_node(state: AgentState) -> dict:
    query = state.get("query", "")
    return {"entities": [Entity(name=query, entity_type="query")] if query else []}


async def retrieve_vectors_node(state: AgentState) -> dict:
    bundle = await get_runtime().retrieve_bundle(state.get("query", ""))
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
    context_parts = [
        (
            f"EVIDENCE_ID={item.chunk_id}\n"
            f"DOCUMENT_ID={item.document_id}\n"
            f"TITLE={item.title}\n"
            f"SECTION={item.section_title}\n"
            f"TEXT={item.content[:2000]}"
        )
        for item in evidence
    ]
    context_parts.extend(_relation_context(relation) for relation in relations)
    from src.config import get_settings

    context = "\n---\n".join(context_parts)
    return {"context": context[: get_settings().max_context_chars]}


async def verify_evidence_node(state: AgentState) -> dict:
    """Fail closed for high-risk claims when no release-scoped evidence survived retrieval."""
    query = state.get("query", "")
    evidence: list[RetrievalResult] = state.get("retrieved_evidence", [])
    if not requires_evidence_verification(query):
        return {"verification_failed": False}
    valid = [
        item for item in evidence
        if item.dataset_id and item.document_id and item.source_start is not None and item.source_end is not None
    ]
    if valid:
        return {"verification_failed": False}
    return {"verification_failed": True, "response": NO_EVIDENCE_RESPONSE}


async def generate_node(state: AgentState) -> dict:
    if state.get("response"):
        return {"response": state["response"]}
    evidence: list[RetrievalResult] = state.get("retrieved_evidence", [])
    if not evidence:
        return {"response": NO_EVIDENCE_RESPONSE}
    response = await get_runtime().generate(state.get("query", ""), state.get("context", ""))
    return {"response": response}


def _sanitize_output(value: str) -> str:
    sanitized = _REASONING_BLOCK.sub("", value).strip()
    sanitized = re.sub(r"^\s*(?:<\/?(?:thinking|analysis|reasoning)>)+\s*", "", sanitized, flags=re.I)
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
    return {
        "response": response,
        "citations": [citation.model_dump() for citation in (
            state.get("direct_citations") or _citations_from_evidence(state.get("retrieved_evidence", []))
        )],
    }
