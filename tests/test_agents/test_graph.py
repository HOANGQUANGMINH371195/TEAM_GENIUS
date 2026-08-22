import os
from unittest.mock import AsyncMock, patch

import pytest

from src.agents.nodes.graphrag_nodes import (
    _claim_facts_supported,
    _deterministic_legal_unit_response,
    generate_node,
    guardrail_node,
    verify_evidence_node,
)
from src.agents.prompts import NO_EVIDENCE_RESPONSE
from src.config import get_settings
from src.models.graph import Citation, RetrievalResult
from src.services.chat import RetrievalBundle


@pytest.fixture(autouse=True)
def reset_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_claim_fact_verifier_rejects_changed_number_and_status_polarity():
    evidence = ["Văn bản có hiệu lực từ ngày 01/07/2026 và còn hiệu lực."]
    assert _claim_facts_supported("Có hiệu lực từ ngày 01/07/2026.", evidence)
    assert not _claim_facts_supported("Có hiệu lực từ ngày 02/07/2026.", evidence)
    assert not _claim_facts_supported("Văn bản hết hiệu lực.", evidence)


def test_langsmith_tracing_is_disabled_before_graph_use():
    import src.agents.graph  # noqa: F401

    assert os.environ.get("LANGCHAIN_TRACING_V2") == "false"
    assert os.environ.get("LANGSMITH_TRACING") == "false"
@pytest.mark.asyncio
async def test_agent_basic_flow():
    evidence = RetrievalResult(
        chunk_id="chunk-1",
        document_id="doc-1",
        title="Luật BHYT",
        content="Mức hưởng BHYT được quy định tại Điều 22.",
        channels=["semantic"],
    )
    with patch("src.agents.nodes.graphrag_nodes.get_runtime") as runtime_factory:
        runtime = runtime_factory.return_value
        runtime.retrieve_bundle = AsyncMock(return_value=RetrievalBundle([evidence], []))
        runtime.generate = AsyncMock(return_value="Câu trả lời grounded.")
        from src.agents.graph import get_agent

        result = await get_agent().ainvoke({"query": "Quyền lợi BHYT?"})

    assert result["response"] == "Câu trả lời grounded."
    assert result["citations"][0]["chunk_id"] == "chunk-1"
    assert result["claims"][0]["claim_type"] == "general"


@pytest.mark.asyncio
async def test_agent_state_structure():
    evidence = RetrievalResult(chunk_id="chunk-1", document_id="doc-1", content="Evidence")
    with patch("src.agents.nodes.graphrag_nodes.get_runtime") as runtime_factory:
        runtime = runtime_factory.return_value
        runtime.retrieve_bundle = AsyncMock(return_value=RetrievalBundle([evidence], []))
        runtime.generate = AsyncMock(return_value="Answer")
        from src.agents.graph import get_agent

        result = await get_agent().ainvoke({"query": "Test query"})

    assert isinstance(result, dict)
    assert "query" in result
    assert "retrieved_evidence" in result


@pytest.mark.asyncio
async def test_metadata_direct_answer_keeps_document_provenance():
    citation = Citation(
        document_id="doc-1", chunk_id="metadata:doc-1", title="Luật BHYT",
        channels=["exact"], evidence_kind="document_metadata",
    )
    with patch("src.agents.nodes.graphrag_nodes.get_runtime") as runtime_factory:
        runtime = runtime_factory.return_value
        runtime.retrieve_bundle = AsyncMock(return_value=RetrievalBundle([], [], "Tên văn bản.", [citation]))
        runtime.generate = AsyncMock()
        from src.agents.graph import get_agent

        result = await get_agent().ainvoke({"query": "Tiêu đề văn bản là gì?"})

    assert result["response"] == "Tên văn bản."
    assert result["citations"] == [citation.model_dump()]
    runtime.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_high_risk_query_without_provenance_is_rejected():
    result = await verify_evidence_node({"query": "Văn bản này còn hiệu lực không?", "retrieved_evidence": []})

    assert result["verification_failed"] is True
    assert "xác minh" in result["response"]


@pytest.mark.asyncio
async def test_official_status_metadata_can_pass_status_gate():
    citation = Citation(
        document_id="doc-1",
        chunk_id="metadata:doc-1",
        dataset_id="release-1",
        title="Luật BHYT",
        quote="Còn hiệu lực",
        evidence_kind="document_metadata",
        provenance_verified=True,
        source_url="https://vbpl.vn/example",
    )
    result = await verify_evidence_node(
        {
            "query": "Văn bản này còn hiệu lực không?",
            "retrieved_evidence": [],
            "direct_citations": [citation],
        }
    )
    assert result["verification_failed"] is False


@pytest.mark.asyncio
async def test_high_risk_guardrail_downgrades_unmapped_claim():
    evidence = RetrievalResult(
        chunk_id="chunk-1",
        document_id="doc-1",
        dataset_id="release-1",
        content="Nguồn chính thức ghi nhận ngày ban hành văn bản.",
        source_start=0,
        source_end=52,
    )
    result = await guardrail_node(
        {
            "query": "Văn bản này còn hiệu lực không?",
            "response": "Văn bản chắc chắn còn hiệu lực.",
            "retrieved_evidence": [evidence],
        }
    )

    assert "chưa đủ cơ sở" in result["response"]
    assert result["claims"]
    assert result["claims"][0]["verification"] in {"partial", "unsupported"}


@pytest.mark.asyncio
async def test_generation_does_not_discard_retrieved_evidence_when_model_falls_back():
    evidence = RetrievalResult(
        chunk_id="chunk-1",
        document_id="doc-1",
        title="Nghị quyết BHYT",
        section_title="Điều 2",
        content="Người cao tuổi chưa có thẻ BHYT được hỗ trợ 30%.",
        dataset_id="release-1",
        source_start=0,
        source_end=52,
    )
    with patch("src.agents.nodes.graphrag_nodes.get_runtime") as runtime_factory:
        runtime_factory.return_value.generate = AsyncMock(
            return_value=(
                "Hiện tại hệ thống không tìm thấy thông tin hoặc văn bản pháp lý phù hợp "
                "để trả lời đầy đủ câu hỏi."
            )
        )
        result = await generate_node(
            {"query": "Đối tượng nào được hỗ trợ?", "context": "evidence", "retrieved_evidence": [evidence]}
        )

    assert "Người cao tuổi" in result["response"]
    assert result["response"] != NO_EVIDENCE_RESPONSE


@pytest.mark.asyncio
async def test_context_can_exceed_public_citation_budget(monkeypatch):
    monkeypatch.setenv("MAX_LLM_EVIDENCE", "12")
    monkeypatch.setenv("MAX_CITATIONS", "8")
    get_settings.cache_clear()
    evidence = [
        RetrievalResult(
            chunk_id=f"chunk-{index}",
            document_id="doc-1",
            content=f"Evidence {index}",
            score=index / 10,
        )
        for index in range(12)
    ]
    with patch("src.agents.nodes.graphrag_nodes.get_runtime") as runtime_factory:
        runtime = runtime_factory.return_value
        runtime.retrieve_bundle = AsyncMock(return_value=RetrievalBundle(evidence, []))
        runtime.generate = AsyncMock(return_value="Answer")
        from src.agents.graph import get_agent

        result = await get_agent().ainvoke({"query": "Test query"})

    assert "Evidence 0" in runtime.generate.await_args.args[1]
    assert "EVIDENCE_ID=E1" in runtime.generate.await_args.args[1]
    assert "EVIDENCE_ID=E12" in runtime.generate.await_args.args[1]
    assert len(result["citations"]) == 8
    get_settings.cache_clear()


def test_legal_unit_formatter_is_stable_and_deduplicated():
    evidence = [
        RetrievalResult(
            chunk_id="chunk-1", document_id="doc-1", unit_id="unit-1", section_title="a)",
            content="Điều kiện thứ nhất.",
        ),
        RetrievalResult(
            chunk_id="chunk-2", document_id="doc-1", unit_id="unit-1", section_title="a)",
            content="Bản trùng không được lặp.",
        ),
        RetrievalResult(
            chunk_id="chunk-3", document_id="doc-1", unit_id="unit-2", section_title="b)",
            content="Điều kiện thứ hai.",
        ),
    ]
    formatted = _deterministic_legal_unit_response(evidence)
    assert formatted.count("a):") == 1
    assert "Điều kiện thứ nhất" in formatted
    assert "Điều kiện thứ hai" in formatted
    assert "Bản trùng" not in formatted
