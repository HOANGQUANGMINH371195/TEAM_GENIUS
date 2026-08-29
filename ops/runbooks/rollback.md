# Release rollback runbook

Rollback is a pointer switch to a previously verified release, not an ad-hoc
database restore.

1. Freeze publication and capture the active/previous release IDs and
   generation from `ops.active_release` and `dataset_state`.
2. Confirm the target release has a checked, recoverable PostgreSQL backup,
   matching Qdrant points/artifact, and matching Neo4j nodes/edges.
3. Restore into a disposable target first when any artifact is uncertain;
   refuse a non-empty Neo4j target unless it is explicitly disposable.
4. Run `verify_live_corpus_parity.py` against the target release. If the source
   fingerprint or any projection differs, leave the active pointer unchanged.
5. Use the guarded `ops.activate_release()` transition, then restart workers
   and verify `/ready`, a deterministic exact query, a calculator fixture, a
   document HTML citation, and a streaming response.
6. Monitor error rate, latency, cache isolation, and provider failures for the
   canary window. Keep the failed release and its checksums for diagnosis.

Never delete the active or rollback release during an incident. Superseded
Neo4j snapshots may be pruned only through the retention runbook after backup,
parity, and rollback evidence are attached.

