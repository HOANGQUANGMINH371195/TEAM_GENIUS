from src.services.vbpl_ingest import (
    _canonical_reference_type,
    _relationship_display,
    _relationship_id,
    _safe_relationship_label,
)


def test_neo4j_transaction_context_awaits_factory():
    import asyncio

    from src.services.vbpl_ingest import _neo4j_transaction_context

    class Transaction:
        committed = False

        async def commit(self):
            self.committed = True

        async def rollback(self):
            raise AssertionError("rollback should not run")

    class Session:
        async def begin_transaction(self):
            return Transaction()

    async def check():
        async with _neo4j_transaction_context(Session()) as transaction:
            assert isinstance(transaction, Transaction)
        assert transaction.committed

    asyncio.run(check())


def test_reference_type_helpers_preserve_unknown_values_safely():
    raw = {"kind": "new/type", "value": 7}
    assert _canonical_reference_type(raw) == '{"kind":"new/type","value":7}'
    assert _relationship_display(raw) == '{"kind":"new/type","value":7}'
    label = _safe_relationship_label(raw)
    assert label.startswith("REL_VBPL_")
    assert label.replace("_", "").isalnum()


def test_relationship_id_is_stable_and_distinguishes_targets():
    ref = {"reference_id": "ref-1", "target_id": "target-1", "reference_type": 99}
    assert _relationship_id("source-1", ref) == _relationship_id("source-1", dict(ref))
    assert _relationship_id("source-1", ref) != _relationship_id("source-1", {**ref, "target_id": "target-2"})


def test_relationship_label_hash_distinguishes_colliding_slugs():
    assert _safe_relationship_label("a-b") != _safe_relationship_label("a b")
    assert _safe_relationship_label(None).startswith("REL_VBPL_null_")


def test_reference_id_fallback_uses_provisions():
    base = {"target_id": "target", "reference_type": 3, "reference_provisions": ["Điều 1"]}
    changed = {**base, "reference_provisions": ["Điều 2"]}
    assert _relationship_id("source", base) != _relationship_id("source", changed)
