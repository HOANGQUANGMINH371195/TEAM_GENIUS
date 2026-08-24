from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.services.query_rewrite import rewrite_retrieval_query, should_rewrite_query


def test_rewrite_router_only_accepts_open_ended_thematic_queries():
    assert should_rewrite_query("Quyền lợi bảo hiểm y tế được tính thế nào?")
    assert should_rewrite_query("Mức hưởng bảo hiểm y tế hiện nay là bao nhiêu?")
    assert not should_rewrite_query("Hi!")
    assert not should_rewrite_query("Điều 22 Luật BHYT quy định gì?")
    assert not should_rewrite_query("Văn bản 51/2024/QH15 quy định gì?")


@pytest.mark.asyncio
async def test_rewrite_rejects_new_numeric_facts():
    structured = SimpleNamespace(
        ainvoke=AsyncMock(return_value=SimpleNamespace(query="Được hưởng 100% khi vượt 6 lần mức tham chiếu"))
    )
    llm = SimpleNamespace(with_structured_output=lambda *_args, **_kwargs: structured)

    with patch("src.services.query_rewrite.get_rewrite_llm", return_value=llm):
        rewritten = await rewrite_retrieval_query("Người tham gia BHYT 5 năm được hưởng gì?")

    assert rewritten == "Người tham gia BHYT 5 năm được hưởng gì?"


@pytest.mark.asyncio
async def test_rewrite_accepts_paraphrase_that_preserves_numeric_facts():
    structured = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value=SimpleNamespace(
                query="Người tham gia bảo hiểm y tế 5 năm liên tục được hưởng quyền lợi theo quy định"
            )
        )
    )
    llm = SimpleNamespace(with_structured_output=lambda *_args, **_kwargs: structured)

    with patch("src.services.query_rewrite.get_rewrite_llm", return_value=llm):
        rewritten = await rewrite_retrieval_query("Người tham gia BHYT 5 năm được hưởng gì?")

    assert rewritten.startswith("Người tham gia bảo hiểm y tế 5 năm")
