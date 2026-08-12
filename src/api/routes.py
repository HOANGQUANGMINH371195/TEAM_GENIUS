from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from src.agents.graph import get_agent
from src.models.schemas import (
    AgentStatusResponse,
    AnalyzeRequest,
    AnalyzeResponse,
    ChatCitation,
    ChatRequest,
    ChatResponse,
)
from src.services.chat import ChatProviderError, GraphRagUnavailableError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Agent"])


async def run_agent(message: str) -> dict:
    try:
        return await get_agent().ainvoke({"query": message})
    except GraphRagUnavailableError as exc:
        logger.exception("GraphRAG retrieval failure")
        raise HTTPException(status_code=503, detail="GraphRAG service unavailable") from exc
    except ChatProviderError as exc:
        logger.exception("Chat provider failure")
        raise HTTPException(status_code=502, detail="Chat provider unavailable") from exc
    except RuntimeError as exc:
        logger.exception("Agent runtime failure")
        raise HTTPException(status_code=503, detail="Agent service unavailable") from exc
    except Exception as exc:
        logger.exception("Agent request failure")
        raise HTTPException(status_code=500, detail="Internal agent error") from exc


@router.post(
    "/chat",
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
    summary="Chat with the BHYT GraphRAG agent",
    description=(
        "Send a BHYT or hospital-fee question. The agent retrieves evidence from "
        "the active PostgreSQL/pgvector dataset and Neo4j graph before generating "
        "a grounded answer. No evidence means no answer is invented."
    ),
    responses={
        200: {"description": "Grounded answer and provenance-checked citations."},
        422: {"description": "Invalid request payload."},
        502: {"description": "OpenAI chat provider unavailable."},
        503: {"description": "Required GraphRAG dependency unavailable."},
        500: {"description": "Unexpected internal error."},
    },
)
async def chat(request: ChatRequest) -> ChatResponse:
    result = await run_agent(request.message)
    response = result.get("response")
    if not isinstance(response, str) or not response.strip():
        logger.error("Agent returned empty response")
        raise HTTPException(status_code=502, detail="Chat provider returned an empty response")

    citations = []
    for citation in result.get("citations", []):
        if isinstance(citation, dict):
            allowed = {
                key: citation[key]
                for key in ChatCitation.model_fields
                if key in citation
            }
            citations.append(ChatCitation(**allowed))
    return ChatResponse(response=response.strip(), citations=citations)


@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    status_code=status.HTTP_200_OK,
    summary="Analyze user input",
    description="Compatibility endpoint for non-conversational analysis.",
)
async def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
    result = await run_agent(request.message)
    return AnalyzeResponse(analysis=result.get("response", ""))


@router.get(
    "/status",
    response_model=AgentStatusResponse,
    summary="Get agent status",
    description="Check whether the LangGraph GraphRAG agent is available.",
)
async def agent_status() -> AgentStatusResponse:
    return AgentStatusResponse(status="ready", agent="LangGraph GraphRAG Agent v1.0")
