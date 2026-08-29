import pytest

from src.domain.facts import LegalFact
from src.integrations.neo4j import Neo4jGraphStore


class _Result:
    def __init__(self, rows):
        self.rows = rows

    async def data(self):
        return self.rows

    async def single(self):
        return self.rows[0] if self.rows else None


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


@pytest.mark.asyncio
async def test_typed_fact_projection_rejects_unreviewed_or_unanchored_facts():
    store = object.__new__(Neo4jGraphStore)
    pending = LegalFact(
        fact_id="f1", subject="group", predicate="coverage_rate", normalized_value="80%",
        effective_from=None, effective_to=None, jurisdiction="VN", provision_id="u1",
        document_id="d1", unit_id="u1", source_start=0, source_end=4,
        source_sha256="hash", review_status="pending", release_id="snapshot-test",
    )
    with pytest.raises(ValueError, match="accepted"):
        await store.upsert_legal_facts([pending])

    unanchored = LegalFact(
        fact_id="f2", subject="group", predicate="coverage_rate", normalized_value="80%",
        effective_from=None, effective_to=None, jurisdiction="VN", provision_id="u1",
        document_id="d1", unit_id="u1", source_start=None, source_end=None,
        source_sha256="hash", review_status="accepted", release_id="snapshot-test",
    )
    with pytest.raises(ValueError, match="source span"):
        await store.upsert_legal_facts([unanchored])


@pytest.mark.asyncio
async def test_readiness_accepts_additive_audit_nodes_and_edges():
    store, session = _store([{"node_count": 1917, "relationship_count": 213}])

    assert await store.readiness(
        dataset_id="snapshot-1",
        expected_nodes=1901,
        expected_approved_edges=187,
    ) is True
    assert session.params["dataset_id"] == "snapshot-1"
