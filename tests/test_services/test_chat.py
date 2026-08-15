from unittest.mock import AsyncMock, patch

import pytest

from src.models.graph import RetrievalResult
from src.services.chat import GraphRagRuntime, _limit_evidence


def test_limit_evidence_uses_internal_budget():
    evidence = [
        RetrievalResult(
            chunk_id=f"chunk-{index}",
            document_id="doc-1",
            content=f"Evidence {index}",
            score=index / 10,
        )
        for index in range(12)
    ]

    selected = _limit_evidence(evidence, 10)

    assert len(selected) == 10
    assert selected[0].chunk_id == "chunk-11"
    assert selected[-1].chunk_id == "chunk-2"


@pytest.mark.asyncio
async def test_generate_uses_configured_llm():
    runtime = GraphRagRuntime()
    result = type("Message", (), {"content": "Grounded answer"})()
    llm = type("Llm", (), {"ainvoke": AsyncMock(return_value=result)})()

    with patch("src.services.chat.get_llm", return_value=llm):
        answer = await runtime.generate("question", "EVIDENCE_ID=chunk-1")

    assert answer == "Grounded answer"
    llm.ainvoke.assert_awaited_once()
    assert "EVIDENCE_ID=chunk-1" in llm.ainvoke.await_args.args[0][1].content
