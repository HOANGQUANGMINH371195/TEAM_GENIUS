# Offline batch contract

Embedding, extraction and evaluation jobs use immutable input and output
manifests. Each item is keyed by content hash and records model, configuration,
release ID, attempt, cost estimate, start/end time and error class. Retries are
idempotent; poison items are quarantined instead of silently dropped.

Batching is never applied across interactive final answers. A single request
may batch its own subquery embeddings and Qdrant queries, while generation
remains isolated per user. A projection is marked ready only after PostgreSQL,
Qdrant and Neo4j counts and hashes pass parity checks.

`eval/batch_manifest.py` implements the provider-neutral contract: it
deduplicates by release/model/content hash, records retries and poison-item
quarantine, and emits a cost ledger. Provider adapters may submit its JSONL,
but must preserve item IDs and never batch interactive final answers across
users.
