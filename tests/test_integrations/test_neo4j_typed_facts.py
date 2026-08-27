import pytest

from src.integrations.neo4j import Neo4jGraphStore


class _Result:
    def __init__(self, rows):
        self.rows = rows

    async def data(self):
        return self.rows


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.query = ""
        self.params = {}

    async def run(self, query, **params):
        self.query = query
        self.params = params
        return _Result(self.rows)


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Driver:
    def __init__(self, session):
        self.session_value = session

    def session(self, **_kwargs):
        return _SessionContext(self.session_value)


def _store(rows):
    session = _Session(rows)
    store = object.__new__(Neo4jGraphStore)
    store.database = "neo4j"
    store.driver = _Driver(session)
    return store, session


@pytest.mark.asyncio
async def test_bounded_typed_ppr_caps_depth_fanout_and_only_returns_typed_relations():
    store, session = _store(
        [
            {
                "subject": "học sinh",
                "value": "50%",
                "predicate": "coverage_rate",
                "fact_id": "fact-1",
                "document_id": "doc-1",
                "unit_id": "unit-1",
            }
        ]
    )

    relations = await store.bounded_typed_ppr(
        ["học sinh", "người lao động", "người cao tuổi", "người khác"],
        dataset_id="snapshot-1",
        depth=99,
        fanout=99,
    )

    assert len(relations) == 1
    assert relations[0].relation_type == "coverage_rate"
    assert relations[0].source_id == "doc-1"
    assert relations[0].target_id == "unit-1"
    assert "1..2" in session.query
    assert session.params["subjects"] == ["học sinh", "người lao động", "người cao tuổi"]
    assert session.params["limit"] == 6
    assert "review_status = 'accepted'" in session.query
    assert "dataset_id = $dataset_id" in session.query


@pytest.mark.asyncio
async def test_typed_fact_expansion_is_release_scoped_and_bounded():
    store, session = _store([])

    result = await store.expand_typed_facts(
        ["học sinh"] * 100,
        dataset_id="snapshot-1",
        limit=999,
    )

    assert result == []
    assert len(session.params["subjects"]) == 1
    assert session.params["limit"] == 100
    assert "edge.review_status = 'accepted'" in session.query
