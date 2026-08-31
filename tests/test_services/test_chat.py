import asyncio
from contextlib import asynccontextmanager
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.agents.nodes.graphrag_nodes import _pack_context, _source_backed_fallback
from src.integrations.qdrant import VectorHit
from src.models.graph import DocumentCandidate, RetrievalResult
from src.models.schemas import GroundedAnswer
from src.services.chat import (
    GraphRagRuntime,
    RetrievalBundle,
    _answer_cache_allowed,
    _apply_document_ranking_metadata,
    _format_metadata_answer,
    _limit_evidence,
    render_grounded_answer,
)


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


def test_lexical_passage_metadata_is_available_for_public_citations():
    passage = RetrievalResult(
        chunk_id="chunk-1",
        document_id="doc-1",
        dataset_id="release-1",
        content="Quy định về quyền lợi BHYT.",
        channels=["lexical"],
    )

    _apply_document_ranking_metadata(
        [passage],
        {
            "doc-1": {
                "document_number": "51/2024/QH15",
                "document_type": "Luật",
                "issued_date": "27/11/2024",
                "effective_from": "01/07/2025",
                "effective_to": "",
                "legal_status": "Còn hiệu lực",
                "legal_status_verified": True,
                "issuer": "Quốc hội",
                "jurisdiction": "Trung ương",
                "source_url": "https://example.invalid/source",
                "source_checked_at": "2026-08-28",
                "categories": ["bhyt"],
            }
        },
    )

    assert passage.document_number == "51/2024/QH15"
    assert passage.legal_status_verified is True


def test_source_backed_fallback_never_invents_missing_numeric_value():
    evidence = [
        RetrievalResult(
            chunk_id="chunk-1",
            document_id="doc-1",
            dataset_id="release-1",
            section_title="Mức đóng và hỗ trợ",
            content="Học sinh, sinh viên tự đóng và được ngân sách nhà nước hỗ trợ một phần mức đóng.",
            text_sha256=sha256(
                "Học sinh, sinh viên tự đóng và được ngân sách nhà nước hỗ trợ một phần mức đóng.".encode()
            ).hexdigest(),
        )
    ]

    fallback = _source_backed_fallback(
        "Học sinh tham gia BHYT năm 2026 phải đóng bao nhiêu và được Nhà nước hỗ trợ thế nào?",
        evidence,
    )

    assert "ngân sách nhà nước hỗ trợ" in fallback
    assert "2026" not in fallback
    assert "số tiền cụ thể" in fallback


def test_metadata_status_fails_closed_without_verified_source():
    document = DocumentCandidate(
        document_id="doc-1",
        title="Văn bản",
        so_ky_hieu="123/2020/TT-BYT",
        legal_status="Còn hiệu lực",
        answer_ready=True,
    )
    answer = _format_metadata_answer("Văn bản 123/2020/TT-BYT còn hiệu lực không?", document)
    assert "chưa xác minh từ nguồn chính thức" in answer


def test_metadata_category_uses_user_facing_labels():
    document = DocumentCandidate(
        document_id="doc-1",
        title="Văn bản",
        so_ky_hieu="123/2020/TT-BYT",
        categories=["bhyt"],
        answer_ready=True,
    )
    answer = _format_metadata_answer("Văn bản 123/2020/TT-BYT thuộc nhóm nào?", document)
    assert "Nhóm nội dung của văn bản số hiệu 123/2020/TT-BYT là bảo hiểm y tế (BHYT)" in answer
    assert "Văn bản 123/2020/TT-BYT:" not in answer


def test_metadata_title_and_status_answers_are_intent_focused():
    document = DocumentCandidate(
        document_id="doc-1",
        title="Nghị quyết về hỗ trợ bảo hiểm y tế",
        so_ky_hieu="60/2026/NQ-HĐND",
        ngay_co_hieu_luc="01/07/2026",
        legal_status="Còn hiệu lực",
        legal_status_verified=True,
        categories=["bhyt"],
        answer_ready=True,
    )
    title = _format_metadata_answer("Văn bản 60/2026/NQ-HĐND có tên đầy đủ là gì?", document)
    status = _format_metadata_answer("Văn bản 60/2026/NQ-HĐND có hiệu lực từ ngày nào?", document)
    citation = _format_metadata_answer("Văn bản 60/2026/NQ-HĐND có hiệu lực từ ngày nào?", document, include_context=True)
    assert title == "Tên đầy đủ của văn bản số hiệu 60/2026/NQ-HĐND là: Nghị quyết về hỗ trợ bảo hiểm y tế."
    assert status == "Văn bản số hiệu 60/2026/NQ-HĐND có hiệu lực từ ngày 01/07/2026 và có tình trạng Còn hiệu lực."
    assert "Văn bản số hiệu 60/2026/NQ-HĐND:" in citation


def test_context_packer_keeps_complete_evidence_blocks():
    evidence = [
        RetrievalResult(
            chunk_id="chunk-1",
            document_id="doc-1",
            dataset_id="release-1",
            content="A" * 40,
        ),
        RetrievalResult(
            chunk_id="chunk-2",
            document_id="doc-1",
            dataset_id="release-1",
            content="B" * 40,
        ),
    ]
    context = _pack_context(evidence, [], 180)
    assert "NGUỒN THỨ 1" in context
    assert "NGUỒN THỨ 2" not in context
    assert "doc-1" not in context
    assert "release-1" not in context
    assert context.endswith("A" * 40)


def test_context_packer_includes_public_temporal_metadata_without_storage_ids():
    evidence = [
        RetrievalResult(
            chunk_id="private-chunk",
            document_id="private-document",
            dataset_id="private-release",
            content="Quy định hiện hành.",
            title="Luật Bảo hiểm y tế",
            document_number="51/2024/QH15",
            document_type="Luật",
            effective_from="01/07/2025",
            legal_status="Còn hiệu lực",
            legal_status_verified=True,
        )
    ]

    context = _pack_context(evidence, [], 2_000)

    assert "LOẠI VĂN BẢN: Luật" in context
    assert "HIỆU LỰC TỪ: 01/07/2025" in context
    assert "TÌNH TRẠNG ĐÃ KIỂM TRA: Còn hiệu lực" in context
    assert "private-chunk" not in context
    assert "private-document" not in context
    assert "private-release" not in context


def test_context_packer_honors_token_budget():
    evidence = [
        RetrievalResult(
            chunk_id=f"chunk-{index}",
            document_id="doc-1",
            dataset_id="release-1",
            content="Thông tin pháp lý có kiểm chứng " * 80,
        )
        for index in range(3)
    ]
    context = _pack_context(evidence, [], 100_000, token_budget=600, model="gpt-4o-mini")
    assert "NGUỒN THỨ 1" in context
    assert "NGUỒN THỨ 3" not in context


def test_answer_cache_excludes_temporal_and_high_risk_intents():
    assert _answer_cache_allowed("Quyền lợi BHYT là gì?")
    assert not _answer_cache_allowed("Văn bản này còn hiệu lực không?")
    assert not _answer_cache_allowed("Mức chi trả là bao nhiêu?")


@pytest.mark.asyncio
async def test_social_fast_path_makes_zero_retrieval_provider_calls():
    runtime = GraphRagRuntime()
    runtime._retrieve = AsyncMock()

    bundle = await runtime.retrieve_bundle("Hi!")

    assert bundle.direct_response == (
        "Xin chào! Tôi có thể hỗ trợ bạn tra cứu thông tin BHYT và viện phí."
    )
    assert bundle.evidence == []
    runtime._retrieve.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_authority_seed_is_coalesced_per_query_and_release():
    runtime = GraphRagRuntime()
    repository = SimpleNamespace(
        current_authority_document_ids=AsyncMock(return_value=["doc-current"])
    )

    results = await asyncio.gather(*(
        runtime._current_authority_ids(
            repository,
            query="same question",
            dataset_id="release-1",
            limit=8,
        )
        for _ in range(4)
    ))

    assert results == [["doc-current"]] * 4
    repository.current_authority_document_ids.assert_awaited_once()


@pytest.mark.asyncio
async def test_current_authority_seed_does_not_cross_contaminate_queries():
    runtime = GraphRagRuntime()
    repository = SimpleNamespace(
        current_authority_document_ids=AsyncMock(side_effect=[["doc-a"], ["doc-b"]])
    )

    first = await runtime._current_authority_ids(
        repository, query="question a", dataset_id="release-1", limit=8
    )
    second = await runtime._current_authority_ids(
        repository, query="question b", dataset_id="release-1", limit=8
    )

    assert first == ["doc-a"]
    assert second == ["doc-b"]
    assert repository.current_authority_document_ids.await_count == 2


@pytest.mark.asyncio
async def test_generate_uses_configured_llm():
    runtime = GraphRagRuntime()
    result = type("Message", (), {"content": "Grounded answer"})()
    llm = type("Llm", (), {"ainvoke": AsyncMock(return_value=result)})()

    with patch("src.services.chat.get_llm", return_value=llm):
        answer = await runtime.generate("question", "NGUỒN THỨ 1\nNỘI DUNG: nội dung")

    assert answer == "Grounded answer"
    llm.ainvoke.assert_awaited_once()
    assert "NGUỒN THỨ 1" in llm.ainvoke.await_args.args[0][1].content


@pytest.mark.asyncio
async def test_generate_uses_strict_grounded_schema_and_public_renderer():
    runtime = GraphRagRuntime()
    expected = GroundedAnswer(
        conclusion="Được hưởng theo điều kiện của nguồn.",
        conditions=["Có đủ điều kiện được nêu trong văn bản."],
        exceptions=["Không áp dụng cho trường hợp bị loại trừ."],
    )
    structured = type("Structured", (), {})()
    structured.ainvoke = AsyncMock(return_value=expected)
    llm = type("Llm", (), {})()
    llm.with_structured_output = lambda *_args, **_kwargs: structured

    with patch("src.services.chat.get_llm", return_value=llm):
        answer = await runtime.generate("question", "NGUỒN THỨ 1\nNỘI DUNG: nội dung")

    assert answer == render_grounded_answer(expected)
    assert "Điều kiện:" in answer
    assert "Ngoại lệ:" in answer


@pytest.mark.asyncio
async def test_generate_retries_plain_text_after_truncated_structured_json():
    runtime = GraphRagRuntime()
    structured = type("Structured", (), {})()
    structured.ainvoke = AsyncMock(side_effect=ValueError("Invalid JSON: EOF while parsing a string"))
    plain = type("Message", (), {"content": "Mức đóng được xác định theo nhóm tham gia."})()
    llm = type("Llm", (), {})()
    llm.with_structured_output = lambda *_args, **_kwargs: structured
    llm.ainvoke = AsyncMock(return_value=plain)

    with patch("src.services.chat.get_llm", return_value=llm):
        answer = await runtime.generate("Mức đóng BHYT theo từng nhóm là bao nhiêu?", "Nguồn đã xác nhận")

    assert answer == plain.content
    llm.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_cache_is_scoped_to_active_release():
    runtime = GraphRagRuntime()
    runtime._active_release = ("release-1", 10, 0.0)
    result = type("Message", (), {"content": "Grounded answer"})()
    llm = type("Llm", (), {"ainvoke": AsyncMock(return_value=result)})()

    with patch("src.services.chat.get_llm", return_value=llm):
        first = await runtime.generate("same question", "NGUỒN THỨ 1\nNỘI DUNG: nội dung")
        second = await runtime.generate(" same   question ", "NGUỒN THỨ 1\nNỘI DUNG: nội dung")

    assert first == second == "Grounded answer"
    llm.ainvoke.assert_awaited_once()

    runtime._active_release = ("release-2", 11, 0.0)
    with patch("src.services.chat.get_llm", return_value=llm):
        await runtime.generate("same question", "NGUỒN THỨ 1\nNỘI DUNG: nội dung")
    assert llm.ainvoke.await_count == 2

    runtime._active_release = ("release-3", 12, 0.0)
    with patch("src.services.chat.get_llm", return_value=llm):
        await runtime.generate("Văn bản này còn hiệu lực không?", "NGUỒN THỨ 1\nNỘI DUNG: nội dung")
        await runtime.generate("Văn bản này còn hiệu lực không?", "NGUỒN THỨ 1\nNỘI DUNG: nội dung")
    assert llm.ainvoke.await_count == 4


@pytest.mark.asyncio
async def test_embedding_singleflight_deduplicates_concurrent_queries():
    runtime = GraphRagRuntime()
    embed = AsyncMock(return_value=[0.1] * 1536)
    runtime._embeddings = SimpleNamespace(embed_query=embed)

    vectors = await asyncio.gather(*(runtime._embed_query("cùng câu hỏi") for _ in range(5)))

    assert len(vectors) == 5
    assert embed.await_count == 1


@pytest.mark.asyncio
async def test_retrieve_bundle_many_batches_embeddings_and_qdrant(monkeypatch):
    runtime = GraphRagRuntime()
    runtime._embeddings = SimpleNamespace(
        embed_queries=AsyncMock(return_value=[[0.1] * 3, [0.2] * 3]),
    )
    runtime._vector_store = SimpleNamespace(
        search_many=AsyncMock(return_value=[[], []]),
    )
    monkeypatch.setattr("src.services.chat.get_settings", lambda: SimpleNamespace(
        embedding_dimensions=3,
        retrieval_top_k=2,
        retrieval_candidate_k=3,
        semantic_similarity_threshold=0.1,
        max_llm_evidence=8,
        max_chunks_per_document=2,
    ))
    runtime._retrieve_staged = AsyncMock(
        side_effect=lambda query, **_kwargs: RetrievalBundle(
            evidence=[RetrievalResult(chunk_id=query, document_id=query, content=query, score=1.0)],
            relations=[],
        )
    )
    with patch("src.services.chat.session_scope") as scope:
        @asynccontextmanager
        async def fake_scope():
            yield object()
        scope.side_effect = fake_scope
        with patch("src.services.chat.GraphRepository") as repository_factory:
            repository_factory.return_value.current_dataset_release = AsyncMock(return_value=("release", 2))
            result = await runtime.retrieve_bundle_many(["câu hỏi thứ nhất", "câu hỏi thứ hai"])

    runtime._embeddings.embed_queries.assert_awaited_once()
    runtime._vector_store.search_many.assert_awaited_once()
    assert runtime._vector_store.search_many.await_args.kwargs["query_texts"] == [
        "câu hỏi thứ nhất", "câu hỏi thứ hai"
    ]
    assert {item.chunk_id for item in result.evidence} == {"câu hỏi thứ nhất", "câu hỏi thứ hai"}


@pytest.mark.asyncio
async def test_adaptive_retrieval_fuses_original_and_rewrite_by_rank(monkeypatch):
    runtime = GraphRagRuntime()
    original = RetrievalBundle(
        evidence=[
            RetrievalResult(chunk_id="distractor", document_id="old", content="old", score=0.9),
            RetrievalResult(chunk_id="answer", document_id="current", content="answer", score=0.7),
        ],
        relations=[],
    )
    expanded = RetrievalBundle(
        evidence=[
            RetrievalResult(chunk_id="answer", document_id="current", content="answer", score=0.8),
        ],
        relations=[],
    )
    runtime.retrieve_bundle = AsyncMock(side_effect=[original, expanded])
    runtime._rewrite_query = AsyncMock(return_value="điều khoản giả định phù hợp để tìm kiếm")
    monkeypatch.setattr("src.services.chat.should_rewrite_query", lambda _query: True)
    monkeypatch.setattr(
        "src.services.chat.get_settings",
        lambda: SimpleNamespace(
            query_rewrite_enabled=True,
            query_rewrite_timeout_seconds=1,
            max_llm_evidence=8,
            max_chunks_per_document=2,
        ),
    )

    result = await runtime.retrieve_bundle_adaptive("quyền lợi bảo hiểm y tế")

    assert [item.chunk_id for item in result.evidence] == ["answer", "distractor"]
    assert runtime.retrieve_bundle.await_count == 2


@pytest.mark.asyncio
async def test_adaptive_retrieval_falls_back_to_original_on_rewrite_error(monkeypatch):
    runtime = GraphRagRuntime()
    original = RetrievalBundle(
        evidence=[RetrievalResult(chunk_id="original", document_id="doc", content="answer")],
        relations=[],
    )
    runtime.retrieve_bundle = AsyncMock(return_value=original)
    runtime._rewrite_query = AsyncMock(side_effect=RuntimeError("provider unavailable"))
    monkeypatch.setattr("src.services.chat.should_rewrite_query", lambda _query: True)
    monkeypatch.setattr(
        "src.services.chat.get_settings",
        lambda: SimpleNamespace(query_rewrite_enabled=True, query_rewrite_timeout_seconds=1),
    )

    result = await runtime.retrieve_bundle_adaptive("quyền lợi bảo hiểm y tế")

    assert result is original
    runtime.retrieve_bundle.assert_awaited_once()


@pytest.mark.asyncio
async def test_adaptive_retrieval_preserves_strict_lexical_for_rewrite(monkeypatch):
    runtime = GraphRagRuntime()
    original = RetrievalBundle(evidence=[], relations=[])
    expanded = RetrievalBundle(evidence=[], relations=[])
    runtime.retrieve_bundle = AsyncMock(side_effect=[original, expanded])
    runtime._rewrite_query = AsyncMock(return_value="điều khoản bảo hiểm y tế được chi trả")
    monkeypatch.setattr("src.services.chat.should_rewrite_query", lambda _query: True)
    monkeypatch.setattr(
        "src.services.chat.get_settings",
        lambda: SimpleNamespace(
            query_rewrite_enabled=True,
            query_rewrite_timeout_seconds=1,
            max_llm_evidence=8,
            max_chunks_per_document=2,
        ),
    )

    await runtime.retrieve_bundle_adaptive("dịch vụ này có được hưởng không")

    assert runtime.retrieve_bundle.await_args_list[0].kwargs == {}
    assert runtime.retrieve_bundle.await_args_list[1].kwargs == {}


@pytest.mark.asyncio
async def test_readiness_coalesces_concurrent_probes():
    runtime = GraphRagRuntime()
    probe = AsyncMock(return_value={"llm": True, "embedding": True, "database": True, "qdrant": True, "neo4j": True})
    runtime._readiness_probe = probe
    results = await asyncio.gather(*(runtime.readiness() for _ in range(20)))
    assert all(result["qdrant"] for result in results)
    probe.assert_awaited_once()


@pytest.mark.asyncio
async def test_retrieve_nests_child_span_names():
    names: list[str] = []

    @asynccontextmanager
    async def fake_span(name, **_kwargs):
        names.append(name)
        yield SimpleNamespace(update=lambda **_kw: None)

    @asynccontextmanager
    async def fake_session_scope():
        yield object()

    evidence = RetrievalResult(
        chunk_id="chunk-1",
        document_id="doc-1",
        dataset_id="dataset-1",
        content="Evidence",
        text_sha256=sha256(b"Evidence").hexdigest(),
        input_sha256=sha256(b"embedding-input").hexdigest(),
        channels=["semantic"],
    )
    repository = SimpleNamespace(
        current_dataset_release=AsyncMock(return_value=("dataset-1", 1)),
        find_documents=AsyncMock(return_value=[]),
            resolve_legal_units=AsyncMock(return_value=[]),
            expand_sibling_legal_units=AsyncMock(return_value=[]),
            search_lexical=AsyncMock(return_value=[]),
        document_ranking_metadata=AsyncMock(return_value={}),
        hydrate_chunks=AsyncMock(return_value=[evidence]),
    )
    runtime = GraphRagRuntime()
    runtime._embeddings = SimpleNamespace(embed_query=AsyncMock(return_value=[0.1] * 1536))
    runtime._vector_store = SimpleNamespace(
        search=AsyncMock(
            return_value=[
                VectorHit(
                    "chunk-1", "doc-1", "", 0.9, sha256(b"embedding-input").hexdigest()
                )
            ]
        )
    )

    with (
        patch("src.services.chat.trace_span", fake_span),
        patch("src.services.chat.session_scope", fake_session_scope),
        patch("src.services.chat.GraphRepository", return_value=repository),
    ):
        result_evidence, relations = await runtime.retrieve("Quyền lợi BHYT?")

    assert names == [
        "retrieve-context",
        "get-current-dataset",
        "embedding-query",
        "qdrant-search",
    ]
    assert result_evidence[0].chunk_id == "chunk-1"
    assert relations == []
    repository.search_lexical.assert_awaited_once()
    repository.hydrate_chunks.assert_awaited_once()


@pytest.mark.asyncio
async def test_explicit_document_number_hard_scopes_sql_and_qdrant():
    @asynccontextmanager
    async def fake_session_scope():
        yield object()

    content = "Nội dung áp dụng của văn bản được xác định tại đây."
    digest = sha256(content.encode()).hexdigest()
    evidence = RetrievalResult(
        chunk_id="chunk-1",
        document_id="doc-1",
        dataset_id="dataset-1",
        content=content,
        text_sha256=digest,
        input_sha256=sha256(b"embedding-input").hexdigest(),
        channels=["semantic"],
    )
    repository = SimpleNamespace(
        current_dataset_release=AsyncMock(return_value=("dataset-1", 1)),
        find_documents=AsyncMock(return_value=[DocumentCandidate(
            document_id="doc-1",
            title="Văn bản kiểm thử",
            so_ky_hieu="123/2020/TT-BYT",
            answer_ready=True,
        )]),
        resolve_legal_units=AsyncMock(return_value=[]),
        search_lexical=AsyncMock(return_value=[]),
        document_ranking_metadata=AsyncMock(return_value={}),
        hydrate_chunks_with_scope=AsyncMock(return_value=([evidence], [])),
    )
    runtime = GraphRagRuntime()
    runtime._embeddings = SimpleNamespace(embed_query=AsyncMock(return_value=[0.1] * 1536))
    runtime._vector_store = SimpleNamespace(search=AsyncMock(return_value=[
        VectorHit("chunk-1", "doc-1", "", 0.9, sha256(b"embedding-input").hexdigest())
    ]))

    with (
        patch("src.services.chat.session_scope", fake_session_scope),
        patch("src.services.chat.GraphRepository", return_value=repository),
    ):
        bundle = await runtime.retrieve_bundle("Nội dung văn bản 123/2020/TT-BYT là gì?")

    assert bundle.evidence[0].document_id == "doc-1"
    assert repository.search_lexical.await_args.kwargs["document_ids"] == ["doc-1"]
    assert runtime._vector_store.search.await_args.kwargs["document_ids"] == ["doc-1"]


@pytest.mark.asyncio
async def test_thematic_retrieval_does_not_promote_unscored_sibling_units():
    @asynccontextmanager
    async def fake_session_scope():
        yield object()

    content = "Quyền lợi bảo hiểm y tế được quy định tại đây."
    evidence = RetrievalResult(
        chunk_id="chunk-1",
        document_id="doc-1",
        dataset_id="dataset-1",
        content=content,
        text_sha256=sha256(content.encode()).hexdigest(),
        input_sha256=sha256(b"embedding-input").hexdigest(),
        channels=["semantic"],
    )
    unrelated_scope = RetrievalResult(
        chunk_id="unit:unrelated",
        document_id="doc-2",
        dataset_id="dataset-1",
        content="Đơn vị lập danh sách cấp thẻ.",
        text_sha256=sha256(b"unrelated").hexdigest(),
        channels=["page_index", "semantic_scope"],
        score=1.0,
    )
    repository = SimpleNamespace(
        current_dataset_release=AsyncMock(return_value=("dataset-1", 1)),
        find_documents=AsyncMock(return_value=[]),
        resolve_legal_units=AsyncMock(return_value=[]),
        search_lexical=AsyncMock(return_value=[]),
        document_ranking_metadata=AsyncMock(return_value={}),
        hydrate_chunks_with_scope=AsyncMock(return_value=([evidence], [unrelated_scope])),
    )
    runtime = GraphRagRuntime()
    runtime._embeddings = SimpleNamespace(embed_query=AsyncMock(return_value=[0.1] * 1536))
    runtime._vector_store = SimpleNamespace(search=AsyncMock(return_value=[
        VectorHit("chunk-1", "doc-1", "", 0.9, sha256(b"embedding-input").hexdigest())
    ]))

    with (
        patch("src.services.chat.session_scope", fake_session_scope),
        patch("src.services.chat.GraphRepository", return_value=repository),
    ):
        bundle = await runtime.retrieve_bundle("Quyền lợi BHYT được áp dụng thế nào?")

    assert [item.chunk_id for item in bundle.evidence] == ["chunk-1"]
    assert repository.hydrate_chunks_with_scope.await_args.kwargs["scope_limit"] == 0
