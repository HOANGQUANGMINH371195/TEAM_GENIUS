from __future__ import annotations

from src.agents.state import AgentState
from src.models.graph import Entity


async def intake_node(state: AgentState) -> dict:
    query = state.get("query", "").strip()
    if not query:
        return {"error": "Query must not be empty"}
    return {"query": query, "analysis": "Query accepted for GraphRAG retrieval"}


async def extract_entities_node(state: AgentState) -> dict:
    # Provider-neutral placeholder until local LLM runtime is selected.
    query = state.get("query", "")
    return {"entities": [Entity(name=query, entity_type="query")] if query else []}


async def retrieve_vectors_node(state: AgentState) -> dict:
    # DB-backed retrieval is injected by GraphRAG service in production path.
    # Keep node deterministic until embedding model is configured.
    return {"vector_results": [], "graph_results": [], "citations": []}


async def assemble_context_node(state: AgentState) -> dict:
    chunks = state.get("vector_results", [])
    relations = state.get("graph_results", [])
    context_parts = [chunk.content for chunk in chunks]
    context_parts.extend(
        f"{relation.source} --{relation.relation_type}--> {relation.target}"
        for relation in relations
    )
    return {"context": "\n---\n".join(context_parts)}


async def generate_node(state: AgentState) -> dict:
    if state.get("error"):
        return {"response": f"Lỗi: {state['error']}"}
    if not state.get("context"):
        return {
            "response": (
                "Chưa có model local hoặc dữ liệu GraphRAG được cấu hình. "
                "Vui lòng cấu hình provider trước khi hỏi dữ liệu."
            )
        }
    return {"response": "Thông tin tham chiếu:\n" + state["context"]}


async def guardrail_node(state: AgentState) -> dict:
    return {"response": state.get("response", "").strip()}
