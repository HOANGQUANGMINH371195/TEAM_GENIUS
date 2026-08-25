# Qdrant — AI implementation contract

Qdrant is a projection, not the source of legal text. Every point must retain
`dataset_id`, `document_id`, `unit_id`, source offsets, `input_sha256` and an
`answer_ready`/status filter. Do not silently change embedding model or
dimension, mix releases, or cite payload text without hydrating the canonical
passage from PostgreSQL.

The production query can fuse named `dense` and `bm25` vectors by reciprocal
rank fusion. BM25 inputs are canonical passage text, never a model-generated
rewrite. An absent/incompatible sparse vector is a dense-only fallback, not a
reason to fail a legal answer. A new hybrid collection needs parity plus a
retrieval benchmark before its alias can be activated.
