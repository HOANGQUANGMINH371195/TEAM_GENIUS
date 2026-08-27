from eval.batch_manifest import BatchManifest
from eval.openai_batch import provider_jsonl, submit_openai_batch


def test_manifest_is_idempotent_and_release_scoped():
    manifest = BatchManifest.build(
        [{"query": "A"}, {"query": "A"}, {"query": "B"}],
        release_id="snapshot-1",
        model="gpt-test",
        input_usd_per_million=1.0,
    )
    assert len(manifest.items) == 2
    assert manifest.items[0].release_id == "snapshot-1"
    assert manifest.manifest_id.startswith("batch-")
    other = BatchManifest.build(
        [{"query": "A"}], release_id="snapshot-2", model="gpt-test"
    )
    assert other.manifest_id != manifest.manifest_id


def test_manifest_retries_and_quarantines_and_accounts_cost():
    manifest = BatchManifest.build(
        [{"query": "A"}], release_id="snapshot-1", model="gpt-test"
    )
    item_id = manifest.items[0].item_id
    manifest.mark_error(item_id, "timeout", max_attempts=2)
    assert manifest.items[0].status == "retryable_error"
    manifest.mark_error(item_id, "timeout", max_attempts=2)
    assert manifest.items[0].status == "quarantined"
    manifest.mark_result(item_id, output_tokens=12, actual_cost_usd=0.25)
    assert manifest.items[0].status == "complete"
    assert manifest.items[0].started_at and manifest.items[0].finished_at
    assert manifest.ledger().output_tokens == 12
    assert manifest.ledger().actual_cost_usd == 0.25
    assert '"manifest"' in manifest.to_jsonl()


def test_provider_manifest_excludes_completed_and_quarantined_items():
    manifest = BatchManifest.build(
        [{"input": "A"}, {"input": "B"}], release_id="snapshot-1", model="gpt-test"
    )
    manifest.mark_result(manifest.items[0].item_id, output_tokens=1)
    payload = provider_jsonl(manifest).decode("utf-8")
    assert manifest.items[0].item_id not in payload
    assert manifest.items[1].item_id in payload


def test_openai_batch_adapter_preserves_manifest_identity():
    class Files:
        async def create(self, **_kwargs):
            return type("Uploaded", (), {"id": "file-1"})()

    class Batches:
        async def create(self, **kwargs):
            assert kwargs["metadata"]["release_id"] == "snapshot-1"
            return type("Batch", (), {"id": "batch-1"})()

    class Client:
        files = Files()
        batches = Batches()

    import asyncio

    manifest = BatchManifest.build([{"input": "A"}], release_id="snapshot-1", model="gpt-test")
    assert asyncio.run(submit_openai_batch(manifest, client=Client())) == "batch-1"
