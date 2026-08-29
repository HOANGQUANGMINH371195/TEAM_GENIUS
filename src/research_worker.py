"""Durable research worker entrypoint for a separate Render worker process.

The worker uses Redis only when explicitly configured.  It invokes the same
LangGraph agent as the API, but persists a bounded public result and never
shares one owner's conversation context with another owner.
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.config import get_settings
from src.services.research_jobs import RedisResearchJobQueue, ResearchJob, create_research_queue


async def execute_research(job: ResearchJob) -> dict[str, Any]:
    from src.agents.graph import get_agent

    output = await get_agent().ainvoke({
        "query": job.query,
        "owner_uid": job.owner_uid,
        "conversation_id": job.conversation_id,
    })
    if not isinstance(output, dict):
        raise TypeError("agent output must be an object")
    # Persist only the public answer/citation envelope.  Internal IDs and raw
    # retrieval chunks stay in the request process and are never queue data.
    return {
        "response": str(output.get("response") or ""),
        "citations": [
            {
                "document_number": str(item.get("document_number") or ""),
                "title": str(item.get("title") or ""),
                "section_title": str(item.get("section_title") or ""),
                "quote": str(item.get("quote") or "")[:1200],
            }
            for item in output.get("citations") or []
            if isinstance(item, dict)
        ][:12],
    }


async def run() -> None:
    settings = get_settings()
    if settings.research_queue_backend != "redis":
        raise RuntimeError("research worker requires RESEARCH_QUEUE_BACKEND=redis")
    queue = create_research_queue(settings=settings)
    if not isinstance(queue, RedisResearchJobQueue):
        raise RuntimeError("durable worker did not receive redis queue")
    try:
        while True:
            await queue.run_once(execute_research, block_seconds=5)
    finally:
        await queue.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
