import logging

from fastapi import APIRouter, HTTPException, status

from src.agents.graph import agent
from src.models.schemas import (
    AgentStatusResponse,
    AnalyzeRequest,
    AnalyzeResponse,
    ChatRequest,
    ChatResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Agent"])


async def run_agent(message: str) -> dict:
    try:
        return await agent.ainvoke({"query": message})
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
    summary="Chat with the BHYT agent",
    description="Send a user question to the LangGraph agent and receive its response.",
)
async def chat(request: ChatRequest) -> ChatResponse:
    return ChatResponse(
        response=(result := await run_agent(request.message)).get("response", ""),
        analysis=result.get("analysis", ""),
    )


@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    status_code=status.HTTP_200_OK,
    summary="Analyze user input",
    description="Run the agent analysis flow without returning a conversational response.",
)
async def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
    result = await run_agent(request.message)
    return AnalyzeResponse(analysis=result.get("analysis", ""))


@router.get(
    "/status",
    response_model=AgentStatusResponse,
    summary="Get agent status",
    description="Check whether the LangGraph agent is available.",
)
async def agent_status() -> AgentStatusResponse:
    return AgentStatusResponse(status="ready", agent="LangGraph Agent v1.0")
