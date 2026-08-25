# Qdrant — developer guide

Qdrant stores derived dense semantic vectors and a sparse BM25 representation;
PostgreSQL remains the source of legal text and citations. Release builders and
parity tools remain in `database/corpus/`; the API adapter is
`src/integrations/qdrant.py`.

Required environment: `QDRANT_URL`, `QDRANT_API_KEY`, `QDRANT_COLLECTION` and
`EMBEDDING_MODEL=text-embedding-3-small` with `EMBEDDING_DIMENSIONS=1536`.
Publish a versioned collection, verify point IDs and input hashes, then move
the active alias atomically. Never commit vector artifacts.

## Hybrid BM25 release

New embedding artifacts retain `lexical_text`, the exact canonical text used
for both dense and sparse indexing. For an older dense-only artifact, export
the matching text from PostgreSQL first:

```bash
uv run --with-requirements requirements/dev.lock python database/corpus/export_sparse_inputs.py \
  --dataset-id snapshot-... --output /tmp/bm25-inputs.jsonl
uv run --with-requirements requirements/dev.lock python database/corpus/qdrant_release.py \
  --artifact-dir data/clean/embeddings-reused/snapshot-... \
  --metadata-csv data/clean/medical_active_v31_fully_reviewed/metadata.csv \
  --lexical-inputs /tmp/bm25-inputs.jsonl
```

The release creates a new physical `medical_legal_hybrid_*` collection with
named `dense` and `bm25` vectors. It performs Qdrant Cloud BM25 inference and
parity checks but does **not** move `medical_legal_active` unless `--activate`
is supplied after an evaluation gate passes. The runtime detects the schema;
an old dense-only collection remains safely queryable.
