# Offline batch contract

Embedding, extraction and evaluation jobs use immutable input and output
manifests. Each item is keyed by content hash and records model, configuration,
release ID, attempt, cost estimate, start/end time and error class. Retries are
idempotent; poison items are quarantined instead of silently dropped.

Batching is never applied across interactive final answers. A single request
may batch its own subquery embeddings and Qdrant queries, while generation
remains isolated per user. A projection is marked ready only after PostgreSQL,
Qdrant and Neo4j counts and hashes pass parity checks.
