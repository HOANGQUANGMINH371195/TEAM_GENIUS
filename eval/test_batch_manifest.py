from eval.batch_manifest import BatchManifest


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
    assert manifest.ledger().output_tokens == 12
    assert manifest.ledger().actual_cost_usd == 0.25
    assert '"manifest"' in manifest.to_jsonl()
