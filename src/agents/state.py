from __future__ import annotations

from typing import TypedDict

from src.models.graph import Citation, Entity, Relation, RetrievalResult


class AgentState(TypedDict, total=False):
    """State passed between GraphRAG LangGraph nodes."""

    query: str
    conversation_id: str
    entities: list[Entity]
    relations: list[Relation]
    vector_results: list[RetrievalResult]
    graph_results: list[Relation]
    citations: list[Citation]
    context: str
    analysis: str
    response: str
    error: str
    metadata: dict
