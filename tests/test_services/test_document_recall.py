from __future__ import annotations

from contextlib import asynccontextmanager
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.integrations.qdrant import VectorHit
from src.models.graph import RetrievalResult
from src.services.chat import GraphRagRuntime


@pytest.mark.asyncio
async def test_clause_question_scans_independently_recalled_document_passages() -> None:
    """A short operative clause must survive even when ANN returns background."""
    @asynccontextmanager
    async def fake_scope():
        yield object()

    background = RetrievalResult(
        chunk_id="background", document_id="background-doc", dataset_id="release",
        content="Quy định chung về việc khám bệnh, chữa bệnh.",
        text_sha256=sha256(b"background").hexdigest(),
        input_sha256=sha256(b"input-background").hexdigest(), channels=["semantic"], score=0.9,
    )
    operative_text = "Trường hợp cấp cứu được hưởng 100% mức hưởng tại bất kỳ cơ sở khám bệnh, chữa bệnh nào."
    operative = RetrievalResult(
        chunk_id="operative", document_id="law-doc", dataset_id="release",
        title="Luật về quyền lợi", document_type="Luật", issued_date="2025-01-01",
        content=operative_text, text_sha256=sha256(operative_text.encode()).hexdigest(),
        channels=["document_recall_operatives"], score=2.0,
    )
    repository = SimpleNamespace(
        current_dataset_release=AsyncMock(return_value=("release", 1)),
        find_documents=AsyncMock(return_value=[]),
        search_title_documents=AsyncMock(return_value=[]),
        search_lexical_document_ids=AsyncMock(return_value=["law-doc"]),
        resolve_legal_units=AsyncMock(return_value=[]),
        search_lexical=AsyncMock(return_value=[]),
        document_ranking_metadata=AsyncMock(return_value={}),
        hydrate_chunks_with_scope=AsyncMock(return_value=([background], [])),
        hydrate_chunks=AsyncMock(return_value=[background]),
        search_document_operatives=AsyncMock(return_value=[operative]),
    )
    runtime = GraphRagRuntime()
    runtime._embeddings = SimpleNamespace(embed_query=AsyncMock(return_value=[0.1] * 1536))
    runtime._vector_store = SimpleNamespace(search=AsyncMock(return_value=[
        VectorHit("background", "background-doc", "", 0.9, sha256(b"input-background").hexdigest())
    ]))

    with (
        patch("src.services.chat.session_scope", fake_scope),
        patch("src.services.chat.GraphRepository", return_value=repository),
    ):
        bundle = await runtime.retrieve_bundle(
            "Tôi điều trị nội trú cấp cứu không có giấy chuyển tuyến thì có được hưởng BHYT không?"
        )

    assert repository.search_lexical_document_ids.await_args.kwargs["limit"] == 60
    assert repository.search_lexical_document_ids.await_count == 1
    assert any(
        "law-doc" in call.args[0]
        for call in repository.search_document_operatives.await_args_list
    )
    assert "operative" in [item.chunk_id for item in bundle.evidence]
