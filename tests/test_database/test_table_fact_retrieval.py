from hashlib import sha256
from types import SimpleNamespace

import pytest

from src.db.repositories import GraphRepository


class _Result:
    def scalar(self):
        return True

    def __iter__(self):
        return iter(
            [
                SimpleNamespace(
                    fact_id="fact-1",
                    subject="học sinh",
                    attribute="mức hỗ trợ",
                    value="50%",
                    document_id="doc-1",
                    legal_unit_id="unit-1",
                    source_fragment_sha256="fragment-hash",
                    title="Luật BHYT",
                    document_number="51/2024/QH15",
                    section_title="Bảng 1, dòng 2",
                    legal_unit_text="Học sinh được ngân sách nhà nước hỗ trợ 50% mức đóng.",
                    source_start=100,
                    source_end=160,
                    text_sha256=sha256(
                        "Học sinh được ngân sách nhà nước hỗ trợ 50% mức đóng.".encode()
                    ).hexdigest(),
                )
            ]
        )


class _Session:
    def __init__(self):
        self.statement = ""

    async def execute(self, statement, _params):
        self.statement = str(statement)
        return _Result()


@pytest.mark.asyncio
async def test_table_fact_retrieval_returns_canonical_unit_text_and_hash():
    session = _Session()
    repository = GraphRepository(session)

    results = await repository.search_table_facts(
        "học sinh mức hỗ trợ", dataset_id="snapshot-1", limit=12
    )

    assert len(results) == 1
    result = results[0]
    assert result.content.startswith("Học sinh được ngân sách")
    assert result.section_title.endswith("học sinh: mức hỗ trợ = 50%")
    assert result.text_sha256 == sha256(result.content.encode()).hexdigest()
    assert result.source_start == 100
    assert result.source_end == 160
    assert "u.text_sha256 <> ''" in session.statement
    assert "f.payload ->> 'review_status'" in session.statement
